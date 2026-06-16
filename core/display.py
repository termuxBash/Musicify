import sys
import subprocess
from PIL import Image, ImageDraw, ImageFont

WIDTH, HEIGHT = 128, 100

def get_font_path():
    font_paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"
    ]
    for path in font_paths:
        try:
            with open(path, "rb"): return path
        except IOError: continue
    return None

def find_max_font_size(text_lines, font_path, max_width, max_height):
    low_size = 5 # Lowered floor to handle massive sentences safely
    high_size = 70
    best_size = low_size
    line_padding = 1

    while low_size <= high_size:
        mid_size = (low_size + high_size) // 2
        font = ImageFont.truetype(font_path, size=mid_size)
        total_height = 0
        fits = True
        
        for line in text_lines:
            bbox = font.getbbox(line)
            if not bbox: continue
            w = bbox[2] - bbox[0]
            h = bbox[3] - bbox[1]
            
            if w > max_width:
                fits = False
                break
            total_height += h + line_padding

        if total_height > 0:
            total_height -= line_padding
        if total_height > max_height:
            fits = False

        if fits:
            best_size = mid_size
            low_size = mid_size + 1
        else:
            high_size = mid_size - 1

    return best_size

def send_maximized_text_to_bose(input_text):
    image = Image.new("L", (WIDTH, HEIGHT), 0)
    draw = ImageDraw.Draw(image)
    font_path = get_font_path()

    # Start with a small font for measurement
    base_font_size = 10 if font_path else 6
    font = ImageFont.truetype(font_path, size=base_font_size) if font_path else ImageFont.load_default()

    words = input_text.split()

    # Step 1: Dynamically pack words into lines that fit WIDTH
    def pack_words(font):
        lines = []
        current_line = ""
        for word in words:
            test_line = f"{current_line} {word}".strip()
            w = font.getbbox(test_line)[2] - font.getbbox(test_line)[0]
            if w <= WIDTH:
                current_line = test_line
            else:
                if current_line:  # push the line we have
                    lines.append(current_line)
                current_line = word
        if current_line:
            lines.append(current_line)
        return lines

    # Step 2: Find the largest font that fits vertically
    if font_path:
        # Binary search over font size
        low, high = 5, 70
        best_font_size = low
        best_lines = [input_text]

        while low <= high:
            mid = (low + high) // 2
            font = ImageFont.truetype(font_path, size=mid)
            lines = pack_words(font)
            line_padding = 1
            total_height = sum((font.getbbox(line)[3] - font.getbbox(line)[1] + line_padding) for line in lines)
            if total_height - line_padding <= HEIGHT:
                best_font_size = mid
                best_lines = lines
                low = mid + 1
            else:
                high = mid - 1

        font = ImageFont.truetype(font_path, size=best_font_size)
    else:
        font = ImageFont.load_default()
        best_lines = pack_words(font)

    # Step 3: Draw the lines centered vertically and horizontally
    line_padding = 1
    line_heights = []
    total_text_height = 0
    for line in best_lines:
        h = font.getbbox(line)[3] - font.getbbox(line)[1]
        line_heights.append(h)
        total_text_height += h + line_padding
    if total_text_height > 0:
        total_text_height -= line_padding

    current_y = max(0, (HEIGHT - total_text_height) // 2)

    for i, line in enumerate(best_lines):
        w = font.getbbox(line)[2] - font.getbbox(line)[0]
        x_position = max(0, (WIDTH - w) // 2)
        draw.text((x_position, current_y), line, fill=255, font=font)
        current_y += line_heights[i] + line_padding

    raw_data = image.tobytes()

    # Step 4: Send to Bose display
    try:
        ssh_command = ["ssh", "bosespeaker", "cat > /dev/fb0"]
        process = subprocess.Popen(ssh_command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        process.communicate(input=raw_data)
        print(f"Pushed to Bose (Font Size {best_font_size}): {best_lines}")
    except Exception as e:
        print(f"Secure transmission bridge failed: {e}")
        
if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 dis.py [Your text]")
        sys.exit(1)
        
    user_string = " ".join(sys.argv[1:])
    send_maximized_text_to_bose(user_string.upper())
