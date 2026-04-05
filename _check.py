with open('d:/bolts/itf_preranking/index.html', encoding='utf-8') as f:
    text = f.read()
# Check close button HTML
idx = text.find('modal-close"')
snippet = text[idx:idx+100]
print(repr(snippet))
# Check for any remaining garbled Latin-1 sequences
import re
garbled = re.findall(r'[\xc2-\xef][\x80-\xbf]+', text)
print('Remaining garbled sequences:', len(garbled), garbled[:5] if garbled else '')
