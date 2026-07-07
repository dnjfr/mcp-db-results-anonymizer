#!/usr/bin/env python3
"""Convert AdventureWorks CSV files from Microsoft SQL Server format to PostgreSQL-compatible format.

Port of the original update_csvs.rb from https://github.com/lorint/AdventureWorks-for-Postgres
"""

import glob
import os
import sys


def process_csv(csv_file: str) -> None:
    with open(csv_file, "rb") as f:
        raw = f.read()

    try:
        text = raw.decode("utf-8-sig")  # strips BOM automatically
    except UnicodeDecodeError:
        return

    had_bom = raw[:3] == b"\xef\xbb\xbf"
    is_pipes = "+|" in text[:200] if text else False
    has_ampipe = "&|" in text[:500] if text else False
    has_geospatial = "\tE6100000010C" in text[:2000] if text else False

    is_needed = had_bom or is_pipes or has_ampipe or has_geospatial
    if not is_needed:
        return

    print(f"Processing {csv_file}")

    if is_pipes:
        output = _convert_pipe_format(text)
    else:
        output = text
        if has_ampipe or has_geospatial:
            output = output.replace('"', '""')
            output = output.replace("&|\n", "\n").replace("&|\r\n", "\n")
            output = output.replace("\tE6100000010C", "\t\\\\xE6100000010C")
            output = output.replace("\r\n", "\n")

    tmp_path = csv_file + ".tmp"
    with open(tmp_path, "w", encoding="utf-8", newline="") as w:
        w.write(output)
    os.replace(tmp_path, csv_file)


def _convert_pipe_format(text: str) -> str:
    output_parts = []
    buffer = ""

    lines = text.split("\n")
    for line in lines:
        line = line.replace("|474946383961", "|\\\\x474946383961")
        line = line.replace('"', '""')

        while "&|" in line:
            end_index = line.index("&|")
            buffer += line[:end_index].strip()

            fields = buffer.split("+|")
            converted = []
            for i, part in enumerate(fields):
                part = part.replace("\x00", "00" if i == 0 and part == "\x00" else "")
                if len(part) >= 2 and part[0] == "<" and part[-1] == ">":
                    converted.append('"' + part + '"')
                elif len(part) >= 3 and part[1] == "<" and part[-1] == ">":
                    converted.append('"' + part[1:] + '"')
                elif "\t" in part:
                    converted.append('"' + part + '"')
                else:
                    converted.append(part)

            output_parts.append("\t".join(converted))
            output_parts.append("\n")
            buffer = ""
            line = line[end_index + 2:]

        stripped = line.rstrip("\r\n")
        if stripped:
            buffer += stripped.replace("\n", "\\n")

    return "".join(output_parts)


if __name__ == "__main__":
    target_dir = sys.argv[1] if len(sys.argv) > 1 else "."
    for csv_file in sorted(glob.glob(os.path.join(target_dir, "*.csv"))):
        process_csv(csv_file)
