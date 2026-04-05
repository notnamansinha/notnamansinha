import re

def update_ascii(filename, ascii_lines):
    with open(filename, 'r', encoding='utf-8') as f:
        content = f.read()

    # Generate new tspans
    tspans = []
    for i, line in enumerate(ascii_lines):
        y_val = 30.0 + (i * 15.2)
        # Escape any special XML characters just in case, though usually none in this ascii
        line = line.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
        tspans.append(f'<tspan x="0" y="{y_val:.1f}">{line}</tspan>')

    new_ascii_block = '<text x="0" y="25" class="ascii">\n' + '\n'.join(tspans) + '\n</text>'
    
    # Replace old text block
    content = re.sub(r'<text x="0" y="25" class="ascii">.*?</text>', new_ascii_block, content, flags=re.DOTALL)

    with open(filename, 'w', encoding='utf-8') as f:
        f.write(content)

with open('new_ascii.txt', 'r', encoding='utf-8') as f:
    lines = f.read().splitlines()

# Strip purely empty lines from start and end
while lines and not lines[0].strip():
    lines.pop(0)
while lines and not lines[-1].strip():
    lines.pop()

update_ascii('assets/dark_mode.svg', lines)
update_ascii('assets/light_mode.svg', lines)
print("Updated successfully!")
