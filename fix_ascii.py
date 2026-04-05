import re

def fix_file(filename):
    with open(filename, 'r', encoding='utf-8') as f:
        content = f.read()

    # Change SVG height to 650px and rect height to 650px to give it plenty of room
    content = re.sub(r'height="[0-9]+px"', 'height="650px"', content, count=2)
    
    # Let's recalibrate the ASCII art.
    # First, let's extract all the tspans.
    def scale_tspan(match):
        tspan_str = match.group(0)
        y_match = re.search(r'y="([0-9\.]+)"', tspan_str)
        if y_match:
            # We know the current y. Let's map it back to index.
            # Current y starts at 30.0, spacing is 13.2
            y_val = float(y_match.group(1))
            index = round((y_val - 30.0) / 13.2)
            # Let's make it taller! Spacing = 16.5px
            new_y = 30 + (index * 16.5)
            # Maybe font-size should also be bigger?
            return re.sub(r'y="[0-9\.]+"', f'y="{new_y:.1f}"', tspan_str)
        return tspan_str

    ascii_block_match = re.search(r'<text x="0" y="25" class="ascii">.*?</text>', content, re.DOTALL)
    if ascii_block_match:
        ascii_block = ascii_block_match.group(0)
        new_ascii_block = re.sub(r'<tspan x="0" y="[0-9\.]+">', scale_tspan, ascii_block)
        content = content.replace(ascii_block, new_ascii_block)

    # Change ascii font-size to 14.5px so it matches the stretched height visually
    content = re.sub(r'\.ascii\s*\{\s*fill:\s*([^;]+);\s*font-size:\s*[0-9\.]+px;\s*\}', r'.ascii {fill: \1; font-size: 14.5px;}', content)

    with open(filename, 'w', encoding='utf-8') as f:
        f.write(content)

fix_file('assets/dark_mode.svg')
fix_file('assets/light_mode.svg')
print("Done")
