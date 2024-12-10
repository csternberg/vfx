import os
import subprocess
import argparse
import sys

def print_help():
    help_text = """
    Simple video frame extraction Python tool v 1.0.
    Syntax: 
    vfx [-R] [-S] [-I] [-N x] [-T x] [-X x] [-Y x] [-P] [-D] [-H] [source_file | source_path]
    -R (recursive): All files in the current or specified directory and subdirectories are processed.
    -S (silent mode): No text output shown.
    -I (info): Only progress is shown, no diagnostic messages from ffmpeg.
    -N x (number): For each specified video file, x evenly spaced video frames are extracted. If ‘0’ is specified all frames are extracted.
    -T x (time): For each x seconds, a frame is extracted. If ‘0’ is specified all frames are extracted.
    -X x (width): Set the desired width in pixels of the extracted images. If -Y is not also used, the height is set automatically to maintain aspect ratio.
    -Y x (height): Set the desired height in pixels of the extracted images. If -X is not also used, the height is set automatically to maintain aspect ratio.
    -P (prompt): For each found video file answer y (yes), n (no) or (a) abort, to process the file or not, or to abort the process all together.
    -D (directory): All extracted files are saved together with the video file. Default is to create a new folder for the extracted frames.
    -H: Show this help text.
    """
    print(help_text)

def check_ffmpeg():
    try:
        subprocess.run(['ffmpeg', '-version'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("ffmpeg is required to run this script. Download ffmpeg from here: https://ffmpeg.org. See ffmpeg documentation for installation information")
        sys.exit(1)

def extract_frames_fallback(video_file, output_dir, frame_count, silent, info, width, height):
    base_name = os.path.splitext(os.path.basename(video_file))[0]
    
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    result = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries', 'stream=duration', '-of', 'default=noprint_wrappers=1:nokey=1', video_file], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    duration = float(result.stdout)
    
    interval = duration / frame_count
    
    for i in range(frame_count):
        timestamp = i * interval
        output_file = os.path.join(output_dir, f"{base_name}_{i+1:03d}.jpg")
        vf_args = f"select='gte(t\\,{timestamp})',setpts=PTS-STARTPTS"
        if width > 0 and height > 0:
            vf_args += f",scale={width}:{height}"
        elif width > 0:
            vf_args += f",scale={width}:-1"
        elif height > 0:
            vf_args += f",scale=-1:{height}"
        
        ffmpeg_command = [
            'ffmpeg',
            '-i', video_file,
            '-vf', vf_args,
            '-vframes', '1',
            '-q:v', '2',
            output_file
        ]
        if silent or info:
            subprocess.run(ffmpeg_command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL if silent else subprocess.PIPE)
        else:
            subprocess.run(ffmpeg_command)

def extract_frames(video_file, output_dir, frame_count, time_interval, silent, info, width, height):
    base_name = os.path.splitext(os.path.basename(video_file))[0]
    
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
    
    try:
        vf_args = ""
        if time_interval > 0:
            vf_args = f'fps=1/{time_interval}'
        else:
            result = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries', 'stream=duration', '-of', 'default=noprint_wrappers=1:nokey=1', video_file], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            duration = float(result.stdout)
            
            interval = duration / frame_count
            vf_args = f'fps=1/{interval}'
        
        if width > 0 and height > 0:
            vf_args += f",scale={width}:{height}"
        elif width > 0:
            vf_args += f",scale={width}:-1"
        elif height > 0:
            vf_args += f",scale=-1:{height}"
        
        output_pattern = os.path.join(output_dir, base_name + '_%03d.jpg')
        ffmpeg_command = [
            'ffmpeg',
            '-i', video_file,
            '-vf', vf_args,
            '-pix_fmt', 'yuv420p',
            '-q:v', '2',
            output_pattern
        ]
        if silent:
            subprocess.run(ffmpeg_command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif info:
            subprocess.run(ffmpeg_command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        else:
            subprocess.run(ffmpeg_command, check=True)
    except subprocess.CalledProcessError:
        if not silent and not info:
            print(f"Fast method failed for {video_file}. Falling back to slower method.")
        extract_frames_fallback(video_file, output_dir, frame_count, silent, info, width, height)

def process_folder(folder, recursive, silent, info, frame_count, time_interval, prompt, same_dir, width, height):
    if recursive:
        for root, _, files in os.walk(folder):
            for filename in files:
                process_file(os.path.join(root, filename), silent, info, frame_count, time_interval, prompt, same_dir, width, height)
    else:
        for filename in os.listdir(folder):
            process_file(os.path.join(folder, filename), silent, info, frame_count, time_interval, prompt, same_dir, width, height)

def process_file(video_file, silent, info, frame_count, time_interval, prompt, same_dir, width, height):
    if video_file.endswith(('.mp4', '.mov', '.avi', '.mkv')):
        if prompt:
            response = input(f"Process {video_file}? (y/n/a) ").strip().lower()
            if response == 'n':
                return
            elif response == 'a':
                print("Process aborted.")
                sys.exit()

        if not silent and info:
            print(f"Processing: {video_file}")
        
        if same_dir:
            output_dir = os.path.dirname(video_file)
        else:
            base_name = os.path.splitext(os.path.basename(video_file))[0]
            output_dir = os.path.join(os.path.dirname(video_file), base_name + '_frames')
            counter = 1
            unique_folder_name = output_dir
            while os.path.exists(unique_folder_name):
                unique_folder_name = f"{output_dir}_{counter}"
                counter += 1
            output_dir = unique_folder_name
        
        extract_frames(video_file, output_dir, frame_count, time_interval, silent, info, width, height)

def main():
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('source', nargs='?', default='.')
    parser.add_argument('-R', action='store_true')
    parser.add_argument('-S', action='store_true')
    parser.add_argument('-I', action='store_true')
    parser.add_argument('-N', type=int, default=16)
    parser.add_argument('-T', type=int, default=0)
    parser.add_argument('-X', type=int, default=0)
    parser.add_argument('-Y', type=int, default=0)
    parser.add_argument('-P', action='store_true')
    parser.add_argument('-D', action='store_true')
    parser.add_argument('-H', action='store_true', dest='help')
    parser.add_argument('-?', action='store_true', dest='help')
    
    args = parser.parse_args()

    check_ffmpeg()  # Check if ffmpeg is available on the system path
    
    if args.help:
        print_help()
        return

    if args.N != 16 and args.T != 0:
        print("Error: Only one of the switches -N or -T can be used at a time.")
        return

    if not isinstance(args.N, int) or not isinstance(args.T, int):
        print("Error: The switches -N and -T require an integer value.")
        return
    
    process_folder(args.source, args.R, args.S, args.I, args.N, args.T, args.P, args.D, args.X, args.Y)

if __name__ == "__main__":
    main()
