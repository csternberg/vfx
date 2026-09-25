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

    def test_default_is_16_frames(self):
        self.copy("clip.mp4")
        self.assertEqual(self.run_vfx("-S", self.dir).returncode, 0)
        self.assertEqual(len(self.images(self.dir / "clip_frames")), 16)

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
        for args in (["-N", 4, "-T", 1], ["-S", "-I"], ["-N", -1], ["-T", "x"],
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
