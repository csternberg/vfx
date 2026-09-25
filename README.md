# VIDEO FRAME EXTRACTION TOOL 2.0.0

`vfx` is a small Python command-line utility that uses the free utility
[ffmpeg](https://ffmpeg.org) to extract still images from video files:
a number of evenly spaced frames, one frame every x seconds, or every frame.

The program was originally created entirely using Microsoft Copilot AI via
Edge, in an iterative process where more and more options were added.
Version 2.0.0 is a rewrite that fixes several crashes and speeds up
extraction.

The program is public domain and open source.

## Requirements

- Python 3.8 or later (standard library only)
- ffmpeg 5.1 or later, including `ffprobe`, on your `PATH`

## Usage

```
vfx [-R] [-S | -I] [-N x | -T x] [-X x] [-Y x] [-F fmt] [-Q x] [-M]
    [-P] [-D] [-L] [-V] [-H] [source_file | source_path]
```

| Switch | Meaning |
|---|---|
| `-R` | Recursive: process the directory and all its subdirectories |
| `-S` | Silent mode: no text output (except `-P` prompts) |
| `-I` | Info mode: show file names and progress only, no diagnostic messages from ffmpeg |
| `-N x` | Extract x evenly spaced frames per video (default 16); `0` extracts all frames |
| `-T x` | Extract one frame every x seconds (decimals allowed, e.g. `0.5`); `0` extracts all frames |
| `-X x` | Image width in pixels; without `-Y` the height keeps the aspect ratio |
| `-Y x` | Image height in pixels; without `-X` the width keeps the aspect ratio |
| `-F fmt` | Image format: `jpg` (default), `png` or `webp` |
| `-Q x` | Image quality 1–100, higher is better (jpg and webp only) |
| `-M` | Name images by timestamp (`clip_00-01-23.500.jpg`) instead of a counter (`clip_001.jpg`) |
| `-P` | Prompt for each video: `y` (yes), `n` (no) or `a` (abort the whole run) |
| `-D` | Save images next to the video instead of in a new `<name>_frames` folder |
| `-L`, `--dry-run` | List what would be processed, without extracting anything |
| `-V`, `--version` | Show the version number |
| `-H`, `-h`, `-?`, `--help` | Show help (quote `'-?'` in zsh) |

`source` is a video file or a directory (default: the current directory).
In a directory, files ending in `.mp4`, `.m4v`, `.mov`, `.avi`, `.mkv` or
`.webm` (any case) are processed. A file named explicitly is processed
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

## Tests

```bash
python3 -m unittest -v test_vfx
```

The tests generate small synthetic clips with ffmpeg. Tests that need
ffmpeg are skipped if it isn't installed.

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
