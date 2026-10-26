#!/usr/bin/env python3
"""Create a candidate AntiMicroX profile for one SDL-mapped combined Joy-Con device."""
import argparse
from pathlib import Path
import re
import xml.etree.ElementTree as ET


def profile(guid):
    if not re.fullmatch(r'[0-9a-fA-F]{32}', guid):
        raise ValueError('Use the 32-hex GUID from antimicrox --list for the combined controller')
    root = ET.Element('gamecontroller', configversion='19')
    ET.SubElement(root, 'guid').text = guid.lower()
    ET.SubElement(root, 'profilename').text = 'Nintekkit combined Joy-Cons (candidate)'
    active = ET.SubElement(ET.SubElement(root, 'sets'), 'set', index='1')

    def binding(parent, tag, index, code, mode='keyboard'):
        elem = ET.SubElement(parent, tag, index=str(index))
        slot = ET.SubElement(ET.SubElement(elem, 'slots'), 'slot')
        ET.SubElement(slot, 'code').text = str(code)
        ET.SubElement(slot, 'mode').text = mode
        return elem

    movement = ET.SubElement(active, 'stick', index='1')
    ET.SubElement(movement, 'deadZone').text = '8000'
    ET.SubElement(movement, 'mode').text = 'standard'
    for direction, key in ((1, 'W'), (3, 'D'), (5, 'S'), (7, 'A')):
        binding(movement, 'stickbutton', direction, hex(ord(key)))
    camera = ET.SubElement(active, 'stick', index='2')
    ET.SubElement(camera, 'deadZone').text = '6500'
    ET.SubElement(camera, 'mode').text = 'standard'
    for direction, code in ((1, 1), (3, 4), (5, 2), (7, 3)):
        elem = binding(camera, 'stickbutton', direction, code, 'mousemovement')
        ET.SubElement(elem, 'mousespeedx').text = '35'
        ET.SubElement(elem, 'mousespeedy').text = '35'
    # SDL controller slots, independent of printed Nintendo/Xbox button labels.
    for index, code in ((1, '0x20'), (2, '0x1000000'), (3, '0x1000020'),
                        (4, '0x45'), (7, '0x1000000'), (8, '0x1000021')):
        binding(active, 'button', index, code)
    # Minus: focus JEI text search. Shift + trigger provides quick stack transfer.
    minus = binding(active, 'button', 5, '0x1000021')
    slot = ET.SubElement(minus.find('slots'), 'slot')
    ET.SubElement(slot, 'code').text = '0x46'
    ET.SubElement(slot, 'mode').text = 'keyboard'
    binding(active, 'button', 9, 2, 'mousebutton')
    binding(active, 'button', 10, 4, 'mousebutton')
    binding(active, 'button', 11, 5, 'mousebutton')
    dpad = ET.SubElement(active, 'dpad', index='1')
    for direction, code in ((1, '0x52'), (2, '0x39'), (4, '0x55'), (8, '0x31')):
        binding(dpad, 'dpadbutton', direction, code)
    for index, mousebutton in ((1, 3), (2, 1)):
        trigger = ET.SubElement(active, 'trigger', index=str(index))
        ET.SubElement(trigger, 'throttle').text = 'positivehalf'
        binding(trigger, 'triggerbutton', 2, mousebutton, 'mousebutton')
    ET.indent(root)
    return ET.tostring(root, encoding='utf-8', xml_declaration=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--guid', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    data = profile(args.guid)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('xb') as handle:
        handle.write(data + b'\n')
    print('Candidate profile written; verify every physical button before gameplay:', args.output)
