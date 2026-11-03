#!/usr/bin/env python3
"""vfx - simple video frame extraction tool.

Extracts still images from video files using ffmpeg: a number of evenly
spaced frames, one frame every x seconds, or every frame.
"""

import argparse
import fnmatch
import glob
import json
import math
import os
import re
import shutil
import subprocess
import sys
from collections import deque
from pathlib import Path

__version__ = "2.4.0"

VIDEO_EXTENSIONS = {
    ".3g2", ".3gp", ".asf", ".avi", ".avs", ".divx", ".dv", ".f4v", ".flv",
    ".gxf", ".h264", ".h265", ".hevc", ".ivf", ".m1v", ".m2t", ".m2ts",
    ".m2v", ".m4v", ".mj2", ".mkv", ".mov", ".mp4", ".mpe", ".mpeg", ".mpg",
    ".mpv", ".mts", ".mxf", ".nut", ".nsv", ".ogm", ".ogv", ".qt", ".rm",
    ".rmvb", ".swf", ".ts", ".vob", ".webm", ".wmv", ".wtv", ".y4m",
}
IMAGE_FORMATS = ("jpg", "png", "webp")
DEFAULT_FRAME_COUNT = 16
DEFAULT_WEBP_QUALITY = 90
DEFAULT_OUTPUT_DIR = "frames"

SILENT, INFO, VERBOSE = 0, 1, 2

# Keys ffmpeg writes with -progress; kept out of the error-tail buffer so a
# real error message isn't pushed out by them.
_PROGRESS_KEYS = {
    "frame", "fps", "stream_0_0_q", "bitrate", "total_size", "out_time_us",
    "out_time_ms", "out_time", "dup_frames", "drop_frames", "speed", "progress",
}


class VfxError(Exception):
    """A problem with one video file; reported, then the run continues."""


class UserAbort(Exception):
    """The user answered 'a' (abort) at a -P prompt."""


# --------------------------------------------------------------------------
# Command line
# --------------------------------------------------------------------------
def non_negative_int(text):
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected a whole number, got '{text}'")
    if value < 0:
        raise argparse.ArgumentTypeError(f"must be 0 or greater, got {value}")
    return value


def non_negative_float(text):
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected a number, got '{text}'")
    if not math.isfinite(value) or value < 0:
        raise argparse.ArgumentTypeError(f"must be 0 or greater, got {text}")
    return value


def quality_value(text):
    value = non_negative_int(text)
    if not 1 <= value <= 100:
        raise argparse.ArgumentTypeError(f"must be between 1 and 100, got {value}")
    return value


_SHORT_SWITCH = re.compile(r"^-([A-Za-z])(.*)$", re.S)


def normalize_argv(argv):
    """Make single-letter switches case-insensitive (-n == -N). Long options
    stay lowercase. Everything after a bare '--' is left alone."""
    result = []
    for i, token in enumerate(argv):
        if token == "--":
            result.extend(argv[i:])
            break
        match = _SHORT_SWITCH.match(token) if not token.startswith("--") else None
        result.append(f"-{match.group(1).upper()}{match.group(2)}" if match else token)
    return result


def build_parser():
    parser = argparse.ArgumentParser(
        prog="vfx",
        add_help=False,
        allow_abbrev=False,
        description=f"Simple video frame extraction tool v{__version__}. "
                    "Extracts still images from video files using ffmpeg.",
        epilog="Examples:\n"
               "  vfx movie.mp4              16 evenly spaced frames from one file\n"
               "  vfx -R -T 10 ~/Videos      one frame every 10 s, all subfolders\n"
               "  vfx -N 0 -F png clip.mov   every frame, as PNG\n"
               "  vfx -L -R .                list what would be processed\n"
               "\n"
               f"Video files are recognised by extension: "
               f"{', '.join(sorted(VIDEO_EXTENSIONS))} (any case).\n"
               "Exit status: 0 success, 1 one or more files failed, "
               "2 invalid command line, 130 interrupted.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "source", nargs="?", default=".",
        help="Video file or directory to process (default: current directory)")
    parser.add_argument(
        "-R", "--recursive", dest="recursive", action="store_true",
        help="Recursive: process the directory and all its subdirectories")

    output = parser.add_mutually_exclusive_group()
    output.add_argument(
        "-S", "--silent", dest="silent", action="store_true",
        help="Silent mode: no text output (except -P prompts)")
    output.add_argument(
        "-I", "--info", dest="info", action="store_true",
        help="Info mode (the default): one progress line per video, "
             "no diagnostic messages from ffmpeg")
    output.add_argument(
        "--verbose", dest="verbose", action="store_true",
        help="Verbose mode: show ffmpeg's own output")

    selection = parser.add_mutually_exclusive_group()
    selection.add_argument(
        "-N", "--count", dest="count", type=non_negative_int, metavar="x",
        help=f"Extract x evenly spaced frames per video (default "
             f"{DEFAULT_FRAME_COUNT}); 0 extracts all frames")
    selection.add_argument(
        "-T", "--interval", dest="interval", type=non_negative_float, metavar="x",
        help="Extract one frame every x seconds (decimals allowed, e.g. 0.5); "
             "0 extracts all frames")

    parser.add_argument(
        "-W", "--match", dest="match", metavar="mask",
        help="Only process videos whose file name matches the wildcard mask "
             "(e.g. -W \"*spain*\"), ignoring case; the extension must still be "
             "a video one. Quote it so the shell doesn't expand it. Applies to "
             "folder scans, not to a file named as the source")
    parser.add_argument(
        "-X", "--width", dest="width", type=non_negative_int, default=0, metavar="x",
        help="Image width in pixels; without -Y the height keeps the aspect ratio")
    parser.add_argument(
        "-Y", "--height", dest="height", type=non_negative_int, default=0, metavar="x",
        help="Image height in pixels; without -X the width keeps the aspect ratio")
    parser.add_argument(
        "-F", "--format", dest="fmt", type=str.lower, choices=IMAGE_FORMATS, default="jpg",
        metavar="fmt",
        help=f"Image format: {', '.join(IMAGE_FORMATS)} (default jpg)")
    parser.add_argument(
        "-Q", "--quality", dest="quality", type=quality_value, metavar="x",
        help="Image quality 1-100, higher is better (jpg and webp only; "
             "default: jpg best quality, webp 90)")
    parser.add_argument(
        "-M", "--timestamps", dest="timestamps", action="store_true",
        help="Name images by their timestamp in the video "
             "(name_HH-MM-SS.mmm.jpg) instead of a counter (name_001.jpg)")
    parser.add_argument(
        "-P", "--prompt", dest="prompt", action="store_true",
        help="Prompt for each video: y (yes), n (no) or a (abort the whole run)")
    parser.add_argument(
        "-D", "--same-dir", dest="same_dir", action="store_true",
        help="Save images next to the video file instead of in a new "
             "<name>_frames folder")
    parser.add_argument(
        "-O", "--output", dest="output", nargs="?", const=DEFAULT_OUTPUT_DIR,
        metavar="xx",
        help=f"Write all images below folder xx (default {DEFAULT_OUTPUT_DIR}), "
             "keeping the source's folder tree, as xx/<subfolder>/<name>_frames. "
             "If xx exists, xx_001, xx_002, ... is used instead. With -D the "
             "images of each source folder share one folder, xx/<subfolder>. "
             "A name containing '.' asks for confirmation. Put the source before -O, or write --output=xx")
    parser.add_argument(
        "-L", "--dry-run", dest="dry_run", action="store_true",
        help="List the videos that would be processed and where the images "
             "would go, without extracting anything")
    parser.add_argument(
        "-V", "--version", action="version", version=f"%(prog)s {__version__}",
        help="Show version number and exit")
    parser.add_argument(
        "-H", "-h", "-?", "--help", action="help",
        help="Show this help text and exit")
    return parser


def resolve_mode(args):
    """Return ('count', n), ('interval', seconds) or ('all', None)."""
    if args.interval is not None:
        return ("all", None) if args.interval == 0 else ("interval", args.interval)
    count = DEFAULT_FRAME_COUNT if args.count is None else args.count
    return ("all", None) if count == 0 else ("count", count)


def describe_mode(mode, value):
    if mode == "count":
        return f"{value} evenly spaced frame{'s' if value != 1 else ''}"
    if mode == "interval":
        return f"1 frame every {value:g} s"
    return "all frames"


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------
class Reporter:
    def __init__(self, verbosity):
        self.verbosity = verbosity
        self._open_width = 0  # width of an unfinished in-place progress line

    def _close_line(self):
        if self._open_width:
            sys.stdout.write("\n")
            self._open_width = 0

    def say(self, message):
        if self.verbosity > SILENT:
            self._close_line()
            print(message, flush=True)

    def error(self, message):
        if self.verbosity > SILENT:
            self._close_line()
            print(message, file=sys.stderr, flush=True)

    def progress(self, text, done=False):
        if self.verbosity == INFO:
            sys.stdout.write("\r" + text.ljust(self._open_width))
            self._open_width = 0 if done else len(text)
            if done:
                sys.stdout.write("\n")
            sys.stdout.flush()


# --------------------------------------------------------------------------
# File discovery
# --------------------------------------------------------------------------
def is_video(path):
    # "._name.mp4" files are macOS resource forks on non-Apple drives.
    return path.suffix.lower() in VIDEO_EXTENSIONS and not path.name.startswith("._")


def find_videos(source, recursive, pattern=None):
    """Return a sorted list of video files. The whole list is collected
    before anything is extracted, so output folders created during the run
    are never walked into. `pattern` (-W) is a case-insensitive wildcard
    matched against file names in folder scans."""
    if source.is_file():
        return [source]  # named explicitly: accept whatever the extension
    if not source.is_dir():
        raise VfxError(f"No such file or directory: {source}")
    wanted = (lambda name: fnmatch.fnmatchcase(name.lower(), pattern.lower())) \
        if pattern else (lambda name: True)
    if not recursive:
        return sorted(p for p in source.iterdir()
                      if p.is_file() and is_video(p) and wanted(p.name))
    videos = []
    for root, dirs, files in os.walk(source):
        dirs.sort()
        videos.extend(Path(root) / f for f in sorted(files)
                      if is_video(Path(f)) and wanted(f))
    return videos


def unique_output_root(name):
    """The -O folder: `name`, or name_001, name_002, ... if it already exists."""
    root = Path(name)
    if not root.name:  # "." or ".."
        root = root.absolute()
    candidate, n = root, 1
    while candidate.exists():
        candidate = root.with_name(f"{root.name}_{n:03d}")
        n += 1
    return candidate


def output_dir_for(video, args):
    if args.out_root:
        # Mirror the video's folder, relative to the source, below the root.
        try:
            relative = video.parent.relative_to(args.source_dir)
        except ValueError:
            relative = Path()
        if args.same_dir:  # -O -D: one folder per source folder, no per-video folder
            return args.out_root / relative
        base = args.out_root / relative / f"{video.stem}_frames"
    elif args.same_dir:
        return video.parent
    else:
        base = video.parent / f"{video.stem}_frames"
    candidate, n = base, 1
    while candidate.exists():
        candidate = base.with_name(f"{base.name}_{n}")
        n += 1
    return candidate


def unique_prefix(directory, stem, ext):
    """A file-name prefix whose images can't collide with existing files
    (relevant with -D, where images share a folder with earlier runs)."""
    candidate, n = stem, 1
    while directory.is_dir() and any(directory.glob(f"{glob.escape(candidate)}_*.{ext}")):
        candidate = f"{stem}_{n}"
        n += 1
    return candidate


def remove_partial(directory, prefix, ext, remove_dir):
    """Delete the images one failed or interrupted extraction wrote."""
    pattern = re.compile(rf"^{re.escape(prefix)}_[\d.-]+\.{ext}$")
    try:
        for path in directory.iterdir():
            if pattern.match(path.name):
                path.unlink()
        if remove_dir and not any(directory.iterdir()):
            directory.rmdir()
    except OSError:
        pass


def remove_empty_parents(directory, root):
    """After a failed -O extraction, drop the now-empty folders it created
    on the way down to `directory`, up to and including the -O root."""
    if not root:
        return
    try:
        parent = directory.parent
        while parent == root or root in parent.parents:
            parent.rmdir()
            parent = parent.parent
    except OSError:
        pass  # not empty (or already gone): stop


# --------------------------------------------------------------------------
# ffmpeg / ffprobe
# --------------------------------------------------------------------------
def missing_tools():
    return [tool for tool in ("ffmpeg", "ffprobe") if shutil.which(tool) is None]


def _to_float(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def probe(video):
    """Return (duration_seconds, estimated_frame_count); either may be None."""
    cmd = ["ffprobe", "-v", "error", "-select_streams", "V:0",
           "-show_entries", "format=duration:stream=duration,avg_frame_rate,nb_frames",
           "-of", "json", str(video)]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        detail = result.stderr.strip().splitlines()
        raise VfxError("ffprobe could not read the file"
                       + (f": {detail[-1]}" if detail else ""))
    data = json.loads(result.stdout or "{}")
    streams = data.get("streams") or []
    if not streams:
        raise VfxError("no video stream found")
    stream = streams[0]
    # The video stream's own duration is the most accurate, but some
    # containers (e.g. .mkv, .webm) only report it for the whole file.
    duration = (_to_float(stream.get("duration"))
                or _to_float(data.get("format", {}).get("duration")))
    frames = _to_float(stream.get("nb_frames"))
    if frames is None and duration:
        num, _, den = str(stream.get("avg_frame_rate", "")).partition("/")
        num, den = _to_float(num), _to_float(den or "1")
        frames = duration * num / den if num and den else None
    return duration, (int(round(frames)) if frames else None)


def scale_filter(width, height):
    if width and height:
        return f"scale={width}:{height}"
    if width:
        return f"scale={width}:-2"
    if height:
        return f"scale=-2:{height}"
    return None


def encoder_args(fmt, quality):
    if fmt == "jpg":
        # ffmpeg's JPEG qscale runs from 2 (best) to 31 (worst).
        qscale = 2 if quality is None else round(2 + (100 - quality) * 29 / 99)
        return ["-q:v", str(qscale)]
    if fmt == "webp":
        return ["-quality", str(DEFAULT_WEBP_QUALITY if quality is None else quality)]
    return []  # png is lossless


def _is_progress_line(line):
    key, sep, _ = line.partition("=")
    return bool(sep) and key in _PROGRESS_KEYS


def run_ffmpeg(args, verbosity, on_frame=None):
    """Run ffmpeg with the given arguments.

    VERBOSE: ffmpeg's own output goes straight to the terminal.
    INFO/SILENT: ffmpeg only logs errors, and stderr is merged into the
    single stdout pipe that is drained here. Opening stdout and stderr as two
    pipes while reading only one can deadlock once the unread pipe fills.
    With on_frame, it is called with the number of frames written so far.
    """
    cmd = ["ffmpeg", "-nostdin", "-hide_banner", "-n"]
    if verbosity == VERBOSE:
        result = subprocess.run(cmd + args)
        if result.returncode != 0:
            raise VfxError(f"ffmpeg exited with code {result.returncode}")
        return

    cmd += ["-loglevel", "error", "-nostats"]
    if on_frame:
        cmd += ["-progress", "pipe:1"]
    process = subprocess.Popen(cmd + args, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, text=True,
                               errors="replace")
    tail = deque(maxlen=8)
    last_frame = -1
    try:
        for line in process.stdout:
            line = line.strip()
            if not line:
                continue
            if not _is_progress_line(line):
                tail.append(line)
            elif on_frame and line.startswith("frame="):
                try:
                    frame = int(line.split("=", 1)[1])
                except ValueError:
                    continue
                if frame != last_frame:
                    last_frame = frame
                    on_frame(frame)
        process.wait()
    except BaseException:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        raise
    if process.returncode != 0:
        raise VfxError("ffmpeg failed" + (": " + " / ".join(tail) if tail else ""))


# --------------------------------------------------------------------------
# Extraction
# --------------------------------------------------------------------------
def format_timestamp(seconds):
    """12.3456 -> '00-00-12.346' (file-name safe, sorts correctly)."""
    total_ms = int(round(seconds * 1000))
    hours, rest = divmod(total_ms, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    secs, ms = divmod(rest, 1000)
    return f"{hours:02d}-{minutes:02d}-{secs:02d}.{ms:03d}"


def counter_width(expected):
    return max(3, len(str(expected))) if expected else 6


def extract_by_count(video, out_dir, prefix, count, duration, args, show):
    """Seek straight to each timestamp (-ss before -i) and decode just one
    frame there, instead of decoding the whole video to keep a few frames."""
    if not duration:
        raise VfxError("cannot determine the video's duration, which -N needs "
                       "(try -T instead)")
    width = counter_width(count)
    video_filter = scale_filter(args.width, args.height)
    for i in range(count):
        # Centre each sample in its slice of the video: this skips the
        # (often black) very first frame and still covers the ending.
        timestamp = (i + 0.5) * duration / count
        label = format_timestamp(timestamp) if args.timestamps else f"{i + 1:0{width}d}"
        output = out_dir / f"{prefix}_{label}.{args.fmt}"
        cmd = ["-ss", f"{timestamp:.3f}", "-i", str(video), "-map", "0:V:0",
               "-frames:v", "1", "-update", "1"]
        if video_filter:
            cmd += ["-vf", video_filter]
        cmd += encoder_args(args.fmt, args.quality) + [str(output)]
        show(i, count)
        run_ffmpeg(cmd, args.verbosity)
        if not output.exists():
            raise VfxError(f"no frame could be decoded at {timestamp:.3f} s")


def extract_stream(video, out_dir, prefix, interval, duration, frames, args, show):
    """One pass over the whole video: every frame, or the first frame of
    each interval-long slice of the video."""
    filters = []
    if interval:
        # Keep the first frame in each interval-long bucket. Unlike
        # fps=1/x, this also keeps the frame at the last interval mark.
        filters.append(f"select=isnan(prev_selected_t)"
                       f"+gt(floor(t/{interval})\\,floor(prev_selected_t/{interval}))")
        expected = math.ceil(duration / interval) if duration else None
    else:
        expected = frames
    video_filter = scale_filter(args.width, args.height)
    if video_filter:
        filters.append(video_filter)

    cmd = ["-i", str(video), "-map", "0:V:0"]
    if filters:
        cmd += ["-vf", ",".join(filters)]
    cmd += ["-fps_mode", "vfr" if interval else "passthrough"]
    cmd += encoder_args(args.fmt, args.quality)
    if args.timestamps:
        # Name each file by its timestamp in milliseconds, renamed below.
        cmd += ["-enc_time_base", "1:1000", "-frame_pts", "1",
                str(out_dir / f"{prefix}_%d.{args.fmt}")]
    else:
        cmd.append(str(out_dir / f"{prefix}_%0{counter_width(expected)}d.{args.fmt}"))
    run_ffmpeg(cmd, args.verbosity, lambda n: show(n, expected))

    if args.timestamps:
        pattern = re.compile(rf"^{re.escape(prefix)}_(\d+)\.{args.fmt}$")
        for path in list(out_dir.iterdir()):
            match = pattern.match(path.name)
            if match:
                label = format_timestamp(int(match.group(1)) / 1000)
                path.rename(path.with_name(f"{prefix}_{label}.{args.fmt}"))


def count_images(directory, prefix, ext):
    pattern = re.compile(rf"^{re.escape(prefix)}_[\d.-]+\.{ext}$")
    return sum(1 for p in directory.iterdir() if pattern.match(p.name))


def process_video(video, args, reporter, index=1, total=1):
    mode, value = resolve_mode(args)
    duration, frames = probe(video)
    out_dir = output_dir_for(video, args)
    new_dir = not out_dir.exists()
    prefix = unique_prefix(out_dir, video.stem, args.fmt)

    position = f"({index} of {total} videos)"

    def show(done, expected):
        of = f" of {expected}" if expected else ""
        reporter.progress(f"{video.name}: generated {done}{of} frames {position}")

    if args.verbosity == VERBOSE:
        reporter.say(f"Processing: {video} -> {out_dir}")
    else:
        show(0, None)
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        if mode == "count":
            extract_by_count(video, out_dir, prefix, value, duration, args, show)
        else:
            extract_stream(video, out_dir, prefix, value, duration, frames, args, show)
        written = count_images(out_dir, prefix, args.fmt)
        if written == 0:
            raise VfxError("no frames were extracted")
    except BaseException:
        remove_partial(out_dir, prefix, args.fmt, new_dir)
        remove_empty_parents(out_dir, args.out_root)
        raise
    final = f"{video.name}: generated {written} of {written} frames {position}"
    if args.verbosity == INFO:
        reporter.progress(final, done=True)
    else:
        reporter.say(final)


def dry_run(video, args):
    mode, value = resolve_mode(args)
    out_dir = output_dir_for(video, args)
    prefix = unique_prefix(out_dir, video.stem, args.fmt)
    print(f"[DRY RUN] {video}: {describe_mode(mode, value)} -> "
          f"{out_dir / f'{prefix}_*.{args.fmt}'}")


def confirm_output_name(name, root):
    """-O values with a '.' in the last path component (movie.mp4, ., ..) are
    probably a mistake, e.g. `-o movie.mp4` swallowing the source. `root` is
    the folder that will actually be used (see unique_output_root)."""
    if "." not in Path(name).name and Path(name).name:
        return True
    if root == Path(name):
        question = (f"You selected '{name}' as output folder, "
                    "do you want to proceed with this? (y/n) ")
    else:
        question = (f"You selected '{name}' as output folder. "
                    f"Do you want to proceed with '{root}' as output folder? (y/n) ")
    try:
        answer = input(question).strip().lower()
    except EOFError:
        return False
    return answer in ("y", "yes")  # anything else aborts


def confirm(video):
    while True:
        try:
            answer = input(f"Process {video}? (y/n/a) ").strip().lower()
        except EOFError:
            raise UserAbort()
        if answer in ("y", "yes"):
            return True
        if answer in ("n", "no"):
            return False
        if answer in ("a", "abort"):
            raise UserAbort()


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------
def main(argv=None):
    args = build_parser().parse_args(
        normalize_argv(sys.argv[1:] if argv is None else [str(a) for a in argv]))
    args.verbosity = SILENT if args.silent else VERBOSE if args.verbose else INFO
    reporter = Reporter(args.verbosity)

    if not args.dry_run:
        missing = missing_tools()
        if missing:
            print(f"vfx: {' and '.join(missing)} not found. ffmpeg (which includes "
                  "ffprobe) is required: download it from https://ffmpeg.org and "
                  "make sure it is on your PATH.", file=sys.stderr)
            return 1

    try:
        videos = find_videos(Path(args.source), args.recursive, args.match)
    except VfxError as exc:
        print(f"vfx: {exc}", file=sys.stderr)
        return 1
    if not videos:
        matching = f" matching '{args.match}'" if args.match else ""
        reporter.say(f"No video files found{matching} in {args.source}")
        return 0

    args.out_root = unique_output_root(args.output) if args.output else None
    if args.output and not args.dry_run and not confirm_output_name(args.output, args.out_root):
        reporter.say("Process aborted.")
        return 0
    args.source_dir = Path(args.source) if Path(args.source).is_dir() else Path(args.source).parent

    done = skipped = failed = 0
    for index, video in enumerate(videos, 1):
        if args.dry_run:
            dry_run(video, args)
            continue
        try:
            if args.prompt and not confirm(video):
                skipped += 1
                continue
            process_video(video, args, reporter, index, len(videos))
            done += 1
        except UserAbort:
            reporter.say("Process aborted.")
            break
        except Exception as exc:  # KeyboardInterrupt deliberately passes through
            failed += 1
            reporter.error(f"Error: {video}: {exc}")

    if not args.dry_run and len(videos) > 1:
        reporter.say(f"Done: {done} processed, {skipped} skipped, {failed} failed.")
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        sys.exit(130)
