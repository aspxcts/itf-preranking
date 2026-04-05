with open('index.html', 'r', encoding='utf-8') as f:
    text = f.read()
fixed = text.encode('cp1252', errors='replace').decode('utf-8', errors='replace')
with open('index.html', 'w', encoding='utf-8') as f:
    f.write(fixed)
print('Done. Lines:', fixed.count('\n'))
checks = [('\u00e2\u009c\u0095', '\u2715'), ('\u00e2\u2013\u00b2', '\u25b2'), ('\u00e2\u2013\u00bc', '\u25bc'), ('\u00e2\u20ac\u201d', '\u2014'), ('\u00e2\u009a\u00a0', '\u26a0')]
for garbled, expected in checks:
    print(f'garbled gone: {garbled not in fixed}, expected present: {expected in fixed}')
