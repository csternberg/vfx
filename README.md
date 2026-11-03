# VIDEO FRAME EXTRACTION TOOL 2.3.0

`vfx` is a small Python command-line utility that uses the free utility
[ffmpeg](https://ffmpeg.org) to extract still images from video files:
a number of evenly spaced frames, one frame every x seconds, or every frame.

The program was originally created entirely using Microsoft Copilot AI via
Edge, in an iterative process where more and more options were added.
Version 2.0.0 is a rewrite that fixes several crashes and speeds up
extraction.

The program is open source, released under the GNU General Public License
v3 (see `LICENSE`).

## Requirements

- Python 3.8 or later (standard library only)
- ffmpeg 5.1 or later, including `ffprobe`, on your `PATH`

## Usage

Single-letter switches are case-insensitive (`-n 5` = `-N 5`); long options are lowercase.

```
vfx [-R] [-S | -I | --verbose] [-N x | -T x] [-X x] [-Y x] [-F fmt] [-Q x] [-M]
    [-P] [-D] [-O [xx]] [-L] [-V] [-H] [source_file | source_path]
```

| Switch | Meaning |
|---|---|
| `-R`, `--recursive` | Recursive: process the directory and all its subdirectories |
| `-S`, `--silent` | Silent mode: no text output (except `-P` prompts) |
| `-I`, `--info` | Info mode (the default): one progress line per video, e.g. `clip.mp4: generated 5 of 16 frames (2 of 7 videos)`, no ffmpeg output |
| `--verbose` | Show ffmpeg's own output |
| `-N x`, `--count x` | Extract x evenly spaced frames per video (default 16); `0` extracts all frames |
| `-T x`, `--interval x` | Extract one frame every x seconds (decimals allowed, e.g. `0.5`); `0` extracts all frames |
| `-X x`, `--width x` | Image width in pixels; without `-Y` the height keeps the aspect ratio |
| `-Y x`, `--height x` | Image height in pixels; without `-X` the width keeps the aspect ratio |
| `-F fmt`, `--format fmt` | Image format: `jpg` (default), `png` or `webp` |
| `-Q x`, `--quality x` | Image quality 1–100, higher is better (jpg and webp only) |
| `-M`, `--timestamps` | Name images by timestamp (`clip_00-01-23.500.jpg`) instead of a counter (`clip_001.jpg`) |
| `-P`, `--prompt` | Prompt for each video: `y` (yes), `n` (no) or `a` (abort the whole run) |
| `-D`, `--same-dir` | Save images next to the video instead of in a new `<name>_frames` folder |
| `-O [xx]`, `--output [xx]` | Write all images below folder `xx` (default `frames`), keeping the source's folder tree: `xx/<subfolder>/<name>_frames/`. If `xx` exists, `xx_001`, `xx_002`, … is used. With `-D`, the images of each source folder share one folder, `xx/<subfolder>/`. A name containing `.` (e.g. `movie.mp4`) asks `y/n` first. Put the source before `-O`, or write `--output=xx`, otherwise the source is taken as `xx` |
| `-L`, `--dry-run` | List what would be processed, without extracting anything |
| `-V`, `--version` | Show the version number |
| `-H`, `-h`, `-?`, `--help` | Show help (quote `'-?'` in zsh) |

`source` is a video file or a directory (default: the current directory).
In a directory, files with any common video extension (`.mp4`, `.mov`,
`.mkv`, `.avi`, `.webm`, `.mpg`, `.wmv`, `.flv`, `.ts`, `.3gp` and about 30
more; any case) are processed. A file named explicitly is processed
whatever its extension.

Examples:

```bash
python3 vfx.py movie.mp4
```

```bash
python3 vfx.py -R -T 10 ~/Videos
```

```bash
python3 vfx.py -N 0 -F png clip.mov
```

### Behaviour notes

- `-N` frames are centred in equal slices of the video, at times
  `(i + 0.5) × duration / N`. This skips the often-black first frame and
  still samples the ending.
- Existing files are never overwritten. A second run creates
  `<name>_frames_1`, or with `-D` uses the prefix `<name>_1_`.
- If a file fails, the error is reported, any partial images are removed,
  and the run continues with the next file.
- Exit status: `0` success, `1` one or more files failed, `2` invalid
  command line, `130` interrupted with Ctrl-C.

## Man page

`vfx.1` is a man page. To install it on Linux or macOS, copy it into a man
directory, for example `sudo cp vfx.1 /usr/local/share/man/man1/`, then run
`man vfx`. To read it without installing, use `man ./vfx.1`.

## Tests

```bash
python3 -m unittest -v test_vfx
```

The tests generate small synthetic clips with ffmpeg. Tests that need
ffmpeg are skipped if it isn't installed.

## Changes in 2.3.0

- **Changed:** `-O` now honours `-D`: all images of a source folder go into
  the same folder below the output folder (`xx/<subfolder>/`).
- **New:** an `-O` name containing a `.` (such as `movie.mp4`) asks for
  confirmation, to catch a source file taken as the output folder.

## Changes in 2.2.0

- **New:** `-O [xx]` / `--output` collects all images in one folder (default
  `frames`), mirroring the source's folder tree.

## Changes in 2.1.0

- **Changed:** the default output is now one progress line per video
  (`clip.mp4: generated 5 of 16 frames (2 of 7 videos)`) instead of ffmpeg's
  own output. `-I` is now the default; the new `--verbose` shows ffmpeg's output.
- **New:** any common video format is found in folder scans (about 40
  extensions, e.g. `.mpg`, `.wmv`, `.flv`, `.ts`, `.3gp`).
- **New:** single-letter switches are case-insensitive, and every switch has a
  long alias (`--recursive`, `--count`, `--same-dir`, ...).
- **New:** man page (`vfx.1`).

## Changes in 2.0.0

- **Fixed:** passing a single video file crashed (`NotADirectoryError`).
- **Fixed:** `-N 0` crashed (division by zero) instead of extracting all frames.
- **Fixed:** `.mkv`/`.webm` files crashed because their duration couldn't be read.
- **Fixed:** upper-case extensions such as `.MP4` and `.MOV` were ignored.
- **Fixed:** `-N` together with `-T` was silently accepted.
- **Fixed:** `-D` could overwrite earlier images, or hang on ffmpeg's hidden
  "overwrite?" question in silent mode.
- **Fixed:** `-T` dropped the frame at the last interval mark (a 10 s video
  with `-T 3` gave 3 frames instead of 4). `-T` now accepts decimals and
  `-T 0` extracts all frames, as documented.
- **Fixed:** embedded cover art in an MP4 could be extracted instead of the video.
- **Faster:** `-N` now seeks directly to each frame instead of decoding the
  whole video. On a 10-minute 720p clip this was 2× faster and used 6× less
  CPU, and the difference grows with video length.
- **New:** `-F` image format, `-Q` quality, `-M` timestamp file names, `-L`
  dry run, `-V`/`--version`, `-h`/`--help`, real progress in `-I` mode,
  exit codes, a run summary, and a unit test suite.
- Missing ffmpeg/ffprobe is now detected up front; `-H` works without ffmpeg.
