"""Tests for vfx.py. Run with:  python3 -m unittest -v test_vfx

The CLI tests generate small synthetic clips with ffmpeg's own testsrc and
sine sources, and are skipped when ffmpeg/ffprobe aren't installed. Every
subprocess call has a timeout so a hang fails the test instead of stalling.
"""

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import vfx

SCRIPT = Path(__file__).with_name("vfx.py")
HAVE_FFMPEG = not vfx.missing_tools()


def make_clip(path, seconds=3, rate=25, audio=True):
    cmd = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
           "-f", "lavfi", "-i", f"testsrc=d={seconds}:s=160x120:r={rate}"]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"sine=d={seconds}", "-shortest"]
    subprocess.run(cmd + [str(path)], check=True, timeout=60)


def image_width(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=width",
                          "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True, timeout=30)
    return int(out.stdout.strip())


class HelperTests(unittest.TestCase):
    def test_format_timestamp(self):
        self.assertEqual(vfx.format_timestamp(0), "00-00-00.000")
        self.assertEqual(vfx.format_timestamp(3723.4567), "01-02-03.457")

    def test_scale_filter(self):
        self.assertIsNone(vfx.scale_filter(0, 0))
        self.assertEqual(vfx.scale_filter(640, 0), "scale=640:-2")
        self.assertEqual(vfx.scale_filter(0, 480), "scale=-2:480")
        self.assertEqual(vfx.scale_filter(640, 480), "scale=640:480")

    def test_jpeg_quality_mapping(self):
        self.assertEqual(vfx.encoder_args("jpg", None), ["-q:v", "2"])
        self.assertEqual(vfx.encoder_args("jpg", 100), ["-q:v", "2"])
        self.assertEqual(vfx.encoder_args("jpg", 1), ["-q:v", "31"])
        self.assertEqual(vfx.encoder_args("png", 50), [])

    def test_resolve_mode(self):
        parse = vfx.build_parser().parse_args
        self.assertEqual(vfx.resolve_mode(parse([])), ("count", 16))
        self.assertEqual(vfx.resolve_mode(parse(["-N", "0"])), ("all", None))
        self.assertEqual(vfx.resolve_mode(parse(["-T", "0"])), ("all", None))
        self.assertEqual(vfx.resolve_mode(parse(["-T", "0.5"])), ("interval", 0.5))

    def test_counter_width(self):
        self.assertEqual(vfx.counter_width(16), 3)
        self.assertEqual(vfx.counter_width(12345), 5)
        self.assertEqual(vfx.counter_width(None), 6)

    def test_normalize_argv_makes_short_switches_case_insensitive(self):
        self.assertEqual(vfx.normalize_argv(["-n", "5", "-f", "png", "-r"]),
                         ["-N", "5", "-F", "png", "-R"])
        # long options, values and everything after "--" are left alone
        self.assertEqual(vfx.normalize_argv(["--count", "5", "-1", "--", "-x"]),
                         ["--count", "5", "-1", "--", "-x"])

    def test_long_aliases_parse(self):
        args = vfx.build_parser().parse_args([
            "--recursive", "--count", "4", "--width", "64", "--height", "48",
            "--format", "png", "--quality", "80", "--timestamps", "--prompt",
            "--same-dir", "--info"])
        self.assertTrue(args.recursive and args.timestamps and args.prompt
                        and args.same_dir and args.info)
        self.assertEqual((args.count, args.width, args.height, args.fmt, args.quality),
                         (4, 64, 48, "png", 80))

    def test_is_video_accepts_common_formats(self):
        for name in ("a.mpg", "a.WMV", "a.flv", "a.ts", "a.3gp", "a.MTS"):
            self.assertTrue(vfx.is_video(Path(name)), name)
        self.assertFalse(vfx.is_video(Path("a.txt")))
        self.assertFalse(vfx.is_video(Path("._a.mp4")))


@unittest.skipUnless(HAVE_FFMPEG, "ffmpeg/ffprobe not installed")
class CliTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.clips = Path(tempfile.mkdtemp(prefix="vfx_clips_"))
        make_clip(cls.clips / "clip.mp4")
        make_clip(cls.clips / "clip.mkv", audio=False)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.clips, ignore_errors=True)

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="vfx_test_"))

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def copy(self, name, as_name=None, subdir=""):
        target = self.dir / subdir / (as_name or name)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(self.clips / name, target)
        return target

    def run_vfx(self, *args, stdin=None):
        return subprocess.run([sys.executable, str(SCRIPT), *map(str, args)],
                              input=stdin, capture_output=True, text=True,
                              timeout=120)

    def images(self, folder, ext="jpg"):
        return sorted(p.name for p in Path(folder).glob(f"*.{ext}"))

    def test_single_file_source(self):
        video = self.copy("clip.mp4")
        result = self.run_vfx("-S", "-N", 4, video)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.images(self.dir / "clip_frames"),
                         ["clip_001.jpg", "clip_002.jpg", "clip_003.jpg", "clip_004.jpg"])

    def test_lowercase_and_long_switches(self):
        video = self.copy("clip.mp4")
        result = self.run_vfx("-n", 2, "--format", "png", video)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.images(self.dir / "clip_frames", "png"),
                         ["clip_001.png", "clip_002.png"])

    def test_progress_line_and_no_ffmpeg_output(self):
        self.copy("clip.mp4", "a.mp4")
        self.copy("clip.mp4", "b.mp4")
        result = self.run_vfx("-N", 2, self.dir)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("a.mp4: generated 2 of 2 frames (1 of 2 videos)", result.stdout)
        self.assertIn("b.mp4: generated 2 of 2 frames (2 of 2 videos)", result.stdout)
        self.assertNotIn("ffmpeg version", result.stdout + result.stderr)

    def test_other_video_extension_found_in_folder(self):
        self.copy("clip.mp4", "movie.MPG")
        self.assertEqual(self.run_vfx("-S", "-N", 1, self.dir).returncode, 0)
        self.assertTrue((self.dir / "movie_frames").is_dir())

    def test_output_folder_keeps_tree_and_is_unique(self):
        self.copy("clip.mp4", "a.mp4")
        self.copy("clip.mp4", "b.mp4", subdir="sub/deep")
        out = self.dir / "out"
        for _ in range(2):
            result = self.run_vfx("-S", "-R", "-N", 1, self.dir, f"--output={out}")
            self.assertEqual(result.returncode, 0, result.stderr)
        for root in (out, self.dir / "out_001"):
            self.assertEqual(self.images(root / "a_frames"), ["a_001.jpg"])
            self.assertEqual(self.images(root / "sub" / "deep" / "b_frames"), ["b_001.jpg"])
        self.assertFalse((self.dir / "a_frames").exists())

    def test_output_with_same_dir_shares_one_folder_per_source_folder(self):
        self.copy("clip.mp4", "a.mp4")
        self.copy("clip.mp4", "b.mp4")
        self.copy("clip.mp4", "c.mp4", subdir="sub")
        out = self.dir / "out"
        result = self.run_vfx("-S", "-R", "-N", 1, "-D", self.dir, f"--output={out}")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.images(out), ["a_001.jpg", "b_001.jpg"])
        self.assertEqual(self.images(out / "sub"), ["c_001.jpg"])

    def test_output_name_with_dot_needs_confirmation(self):
        video = self.copy("clip.mp4")
        out = self.dir / "movie.mp4"
        result = self.run_vfx("-S", "-N", 1, video, f"--output={out}", stdin="n\n")
        self.assertEqual(result.returncode, 0)
        self.assertIn("proceed", result.stdout)
        self.assertFalse(out.exists())
        result = self.run_vfx("-N", 1, video, f"--output={out}", stdin="y\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.images(out / "clip_frames"), ["clip_001.jpg"])

    def test_output_name_prompt_shows_final_folder_and_only_y_proceeds(self):
        video = self.copy("clip.mp4")
        out = self.dir / "movie.mp4"
        out.write_bytes(b"in the way")
        final = self.dir / "movie.mp4_001"
        result = self.run_vfx("-S", "-N", 1, video, f"--output={out}", stdin="\n")
        self.assertEqual(result.returncode, 0)
        self.assertIn(f"proceed with '{final}' as output folder", result.stdout)
        self.assertFalse(final.exists())
        result = self.run_vfx("-S", "-N", 1, video, f"--output={out}", stdin="maybe\n")
        self.assertFalse(final.exists())
        result = self.run_vfx("-S", "-N", 1, video, f"--output={out}", stdin="y\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.images(final / "clip_frames"), ["clip_001.jpg"])
        self.assertEqual(out.read_bytes(), b"in the way")

    def test_match_filters_by_name_and_keeps_output_placement(self):
        self.copy("clip.mp4", "Spain-2026.mp4")
        self.copy("clip.mp4", "holiday-spain.mp4", subdir="xxx")
        self.copy("clip.mp4", "france.mp4", subdir="xxx")
        result = self.run_vfx("-S", "-R", "-N", 1, "-W", "*spain*", self.dir)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.images(self.dir / "Spain-2026_frames"), ["Spain-2026_001.jpg"])
        self.assertEqual(self.images(self.dir / "xxx" / "holiday-spain_frames"),
                         ["holiday-spain_001.jpg"])
        self.assertFalse((self.dir / "xxx" / "france_frames").exists())
        out = self.dir / "out"
        result = self.run_vfx("-S", "-R", "-N", 1, "-D", "-W", "*SPAIN*.*", self.dir,
                              f"--output={out}")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.images(out), ["Spain-2026_001.jpg"])
        self.assertEqual(self.images(out / "xxx"), ["holiday-spain_001.jpg"])

    def test_output_failure_leaves_no_empty_folders(self):
        (self.dir / "sub").mkdir()
        (self.dir / "sub" / "broken.mp4").write_bytes(b"not a video")
        out = self.dir / "out"
        result = self.run_vfx("-S", "-R", self.dir, f"--output={out}")
        self.assertEqual(result.returncode, 1)
        self.assertFalse(out.exists())

    def test_default_is_16_frames(self):
        self.copy("clip.mp4")
        self.assertEqual(self.run_vfx("-S", self.dir).returncode, 0)
        self.assertEqual(len(self.images(self.dir / "clip_frames")), 16)

    def test_count_larger_than_one_batch(self):
        # 37 samples = three chunks (16 + 16 + 5), run in parallel
        self.copy("clip.mp4")
        for extra in ([], ["-M"], ["--verbose"]):
            with self.subTest(extra=extra):
                shutil.rmtree(self.dir / "clip_frames", ignore_errors=True)
                result = self.run_vfx("-N", 37, *extra, self.dir)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(len(self.images(self.dir / "clip_frames")), 37)
        names = self.images(self.dir / "clip_frames")
        self.assertEqual(len(set(names)), 37)

    def test_all_frames(self):
        video = self.copy("clip.mp4")
        for switch in ("-N", "-T"):
            with self.subTest(switch=switch):
                shutil.rmtree(self.dir / "clip_frames", ignore_errors=True)
                self.assertEqual(self.run_vfx("-S", switch, 0, video).returncode, 0)
                self.assertEqual(len(self.images(self.dir / "clip_frames")), 75)

    def test_interval_includes_last_mark(self):
        video = self.copy("clip.mp4")
        self.assertEqual(self.run_vfx("-S", "-T", 1, "-M", video).returncode, 0)
        self.assertEqual(self.images(self.dir / "clip_frames"),
                         ["clip_00-00-00.000.jpg", "clip_00-00-01.000.jpg",
                          "clip_00-00-02.000.jpg"])

    def test_fractional_interval(self):
        video = self.copy("clip.mp4")
        self.assertEqual(self.run_vfx("-S", "-T", 0.5, video).returncode, 0)
        self.assertEqual(len(self.images(self.dir / "clip_frames")), 6)

    def test_count_timestamps_are_centred(self):
        video = self.copy("clip.mp4")
        self.assertEqual(self.run_vfx("-S", "-N", 3, "-M", video).returncode, 0)
        self.assertEqual(self.images(self.dir / "clip_frames"),
                         ["clip_00-00-00.500.jpg", "clip_00-00-01.500.jpg",
                          "clip_00-00-02.500.jpg"])

    def test_mkv_without_stream_duration(self):
        video = self.copy("clip.mkv")
        result = self.run_vfx("-S", "-N", 2, video)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.images(self.dir / "clip_frames")), 2)

    def test_uppercase_extension_and_recursion(self):
        self.copy("clip.mp4", "UPPER.MP4")
        self.copy("clip.mp4", "deep.mp4", subdir="a/b")
        (self.dir / "._UPPER.MP4").write_bytes(b"resource fork")
        self.assertEqual(self.run_vfx("-S", "-N", 1, self.dir).returncode, 0)
        self.assertTrue((self.dir / "UPPER_frames").is_dir())
        self.assertFalse((self.dir / "a/b/deep_frames").exists())
        self.assertEqual(self.run_vfx("-S", "-R", "-N", 1, self.dir).returncode, 0)
        self.assertTrue((self.dir / "a/b/deep_frames").is_dir())
        self.assertFalse((self.dir / "UPPER_frames/UPPER_frames").exists())

    def test_same_dir_never_overwrites(self):
        video = self.copy("clip.mp4")
        for _ in range(2):
            self.assertEqual(self.run_vfx("-S", "-D", "-N", 2, video).returncode, 0)
        self.assertEqual(self.images(self.dir),
                         ["clip_001.jpg", "clip_002.jpg", "clip_1_001.jpg", "clip_1_002.jpg"])

    def test_second_run_gets_new_folder(self):
        video = self.copy("clip.mp4")
        for _ in range(2):
            self.run_vfx("-S", "-N", 1, video)
        self.assertTrue((self.dir / "clip_frames_1").is_dir())

    def test_format_and_width(self):
        video = self.copy("clip.mp4")
        self.assertEqual(self.run_vfx("-S", "-N", 1, "-F", "PNG", "-X", 64, video).returncode, 0)
        [image] = (self.dir / "clip_frames").glob("*.png")
        self.assertEqual(image_width(image), 64)

    def test_dry_run_writes_nothing(self):
        self.copy("clip.mp4")
        result = self.run_vfx("-L", self.dir)
        self.assertEqual(result.returncode, 0)
        self.assertIn("[DRY RUN]", result.stdout)
        self.assertEqual([p.name for p in self.dir.iterdir()], ["clip.mp4"])

    def test_prompt_skip_and_abort(self):
        self.copy("clip.mp4", "a.mp4")
        self.copy("clip.mp4", "b.mp4")
        result = self.run_vfx("-P", "-N", 1, "-I", self.dir, stdin="n\nmaybe\na\n")
        self.assertEqual(result.returncode, 0)
        self.assertIn("aborted", result.stdout)
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()), ["a.mp4", "b.mp4"])

    def test_bad_file_is_reported_and_run_continues(self):
        (self.dir / "broken.mp4").write_bytes(b"not a video")
        self.copy("clip.mp4")
        result = self.run_vfx("-I", "-N", 1, self.dir)
        self.assertEqual(result.returncode, 1)
        self.assertIn("broken.mp4", result.stderr)
        self.assertTrue((self.dir / "clip_frames").is_dir())
        self.assertFalse((self.dir / "broken_frames").exists())

    def test_usage_errors(self):
        for args in (["-N", 4, "-T", 1], ["-S", "-I"], ["-S", "--verbose"],["-N", -1], ["-T", "x"],
                     ["-Q", 0], ["-F", "gif"]):
            with self.subTest(args=args):
                self.assertEqual(self.run_vfx(*args, self.dir).returncode, 2)

    def test_missing_source(self):
        result = self.run_vfx(self.dir / "nope")
        self.assertEqual(result.returncode, 1)
        self.assertIn("No such file", result.stderr)

    def test_version_and_help(self):
        self.assertEqual(self.run_vfx("-V").stdout.strip(), f"vfx {vfx.__version__}")
        self.assertIn("-T x", self.run_vfx("-?").stdout)


if __name__ == "__main__":
    unittest.main()
