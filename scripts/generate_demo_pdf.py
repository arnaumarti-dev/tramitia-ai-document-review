"""Create a clearly fictional, searchable PDF for TramitIA local QA.

No real names, fiscal IDs, phone numbers, or customer records are required.
Will never overwrite an existing file.
"""
import argparse
import io
from pathlib import Path

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject


def make_demo_pdf() -> bytes:
    writer = PdfWriter()
    page = writer.add_blank_page(width=460, height=600)
    font = DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
    })
    page[NameObject("/Resources")] = DictionaryObject({
        NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})
    })
    stream = DecodedStreamObject()
    stream.set_data(
        b"BT /F1 12 Tf 20 540 Td "
        b"(Solicitud ficticia de subvencion. Declaro este documento de prueba, "
        b"fecha 24/10/2026. Correo ficticio demo@example.invalid. "
        b"Calle Inventada 1, ciudad de pruebas. Firma de prueba.) Tj ET"
    )
    page[NameObject("/Contents")] = stream
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def main():
    parser = argparse.ArgumentParser(description="Generate dummy PDF for TramitIA smoke tests")
    parser.add_argument("--output", default="solicitud-ficticia.pdf")
    args = parser.parse_args()
    destination = Path(args.output)
    # Refuse to replace any existing document, including a previous generated PDF.
    with destination.open("xb") as file:
        file.write(make_demo_pdf())
    print(f"Archivo ficticio creado: {destination}")


if __name__ == "__main__":
    main()
