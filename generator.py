from dataclasses import dataclass
import requests
from time import sleep
import re
from PIL import Image, ImageDraw, ImageFont

from brother_ql.raster import BrotherQLRaster
from brother_ql.backends.helpers import send
from brother_ql.conversion import convert


def beep(count=1):
    for i in range(count):
        print("\a", end="", flush=True)
        if i < count - 1:
            sleep(0.25)


def has_valid_year_at_end(text: str):
    """
    Check if the last 4 characters of a string are digits representing a valid year.

    Args:
        text (str): The string to check

    Returns:
        bool: True if last 4 chars are a valid year, False otherwise
    """
    # Check if string has at least 4 characters
    if len(text) < 4:
        return False

    if text[-1].isalpha():
        text = text[:-1]

    # Extract last 4 characters
    last_four = text[-4:]

    # Check if they are all digits
    if not last_four.isdigit():
        return False

    # Convert to integer and check if it's a reasonable year range
    year = int(last_four)

    # Check if year is in a reasonable range (e.g., 1000-2999)
    # Adjust this range based on your specific needs
    return 1000 <= year <= 2999


@dataclass
class Book:
    isbn: str | None
    ident: str | None
    title: str | None
    authors: str | None
    year: str | None

    def __str__(self) -> str:
        return f"\nTitle: {self.title}\nISBN: {self.isbn}\nAuthors: {self.authors}\nLOC: {self.ident}\nYear: {self.year}\n"

    @staticmethod
    def from_book(book: "Book") -> "Book":
        if book is None:
            return Book(None, None, None, None, None)

        return Book(
            isbn=book.isbn,
            ident=book.ident if book.ident != "" else None,
            title=book.title,
            authors=book.authors,
            year=book.year,
        )


class Generator:
    def __init__(self, model: str, printer: str):
        self._model = model
        self._printer = printer

    @staticmethod
    def _query_book(isbn: str) -> Book:
        response = requests.get(
            f"https://openlibrary.org/api/books?format=json&bibkeys=ISBN:{isbn}&jscmd=data",
            timeout=10000,
        ).json()

        if len(response) == 0:
            return None

        book = response[f"ISBN:{isbn}"]

        if (
            "classifications" in book.keys()
            and "lc_classifications" in book["classifications"].keys()
            and len(book["classifications"]["lc_classifications"]) > 0
        ):
            ident = sorted(book["classifications"]["lc_classifications"], key=len)[-1]
            ident = re.sub("[^0-9a-zA-Z\\.]+", " ", ident)
        else:
            ident = None

        year = None
        if "publish_date" in book.keys():
            year = book["publish_date"]

        return Book(
            isbn=isbn,
            ident=ident,
            title=book["title"],
            authors=" and ".join(map(lambda x: x["name"], book["authors"])),
            year=year,
        )

    @staticmethod
    def store_book(book: Book) -> int:
        with open("current-id.txt", "r+", encoding="utf-8") as uid_f:
            current_uid = int(uid_f.readline())
            uid_f.seek(0)

            current_uid += 1
            uid_f.writelines([str(current_uid)])

        with open("books.csv", "a", encoding="utf-8") as csv:
            csv.writelines(
                [f'{current_uid},"{book.title}","{book.authors}","{book.ident}"\n']
            )
        return current_uid

    @staticmethod
    def _format_ident(call_number: str) -> str:
        """
        Format an LOC call number into lines suitable for labels.
        - Handles optional prefixes.
        - Accepts space between class letters and number (HD 30.22 -> HD30.22).
        - Ensures cutters have leading dots; splits combined cutter sequences like T8E2 -> .T8, .E2.
        - Preserves year suffixes (1983b) and v./c. tokens.
        """
        if not call_number or not call_number.strip():
            return ""

        cn = call_number.strip()
        cn = re.sub(r"\s+", " ", cn)

        parts = []

        # Common prefixes
        prefix_match = re.match(
            r"^(REF|JUV|OVERSIZE|MAPS|DOCS|SERIAL|MICRO|SHELF)\b", cn, re.IGNORECASE
        )
        if prefix_match:
            parts.append(prefix_match.group(1).upper())
            cn = cn[prefix_match.end() :].strip()

        # Main class (letters + number, allow space)
        main_match = re.match(r"^([A-Z]{1,3})\s*(\d+(?:\.\d+)?)", cn, re.IGNORECASE)
        if main_match:
            parts.append(f"{main_match.group(1).upper()}{main_match.group(2)}")
            cn = cn[main_match.end() :].strip()
        else:
            # fallback: take first token
            token = cn.split()[0] if cn else ""
            parts.append(token.upper())
            cn = cn[len(token) :].strip()

        # Space out dots so we can detect dot tokens reliably
        spaced = re.sub(r"\.+", " . ", cn)
        tokens = [t for t in spaced.split() if t]

        normalized = []
        i = 0
        while i < len(tokens):
            t = tokens[i]

            # Attach '.' to next token if token is just a dot
            if t == ".":
                if i + 1 < len(tokens):
                    token = "." + tokens[i + 1]
                    i += 2
                else:
                    token = "."
                    i += 1
            else:
                token = t
                i += 1

            # Handle v./c. token sequences (v . 2 -> v.2)
            m_vc = re.fullmatch(r"([vc])\.(\d+)", token, re.IGNORECASE)
            if m_vc:
                normalized.append(f"{m_vc.group(1).lower()}.{m_vc.group(2)}")
                continue

            # If token is a year with trailing letter already, keep it
            if re.fullmatch(r"\d{4}[A-Za-z]?", token):
                normalized.append(token)
                continue

            # If token is a dot-prefixed chunk that may contain multiple letter+number groups,
            # split it into separate cutters: .T8E2 -> .T8, .E2
            if token.startswith("."):
                core = token[1:]
                # find letter+number groups in sequence
                groups = re.findall(r"([A-Za-z]+)(\d+(?:\.\d+)?)", core)
                if groups:
                    for g in groups:
                        normalized.append("." + g[0].upper() + g[1])
                    continue
                else:
                    # if no letter+number groups, just normalize casing
                    normalized.append("." + core)
                    continue

            # If token looks like letter+digits (no leading dot), treat as cutter and add dot
            if re.fullmatch(r"[A-Za-z]+\d+(?:\.\d+)?", token):
                # split multiple groups if present (e.g., T8E2 -> T8, E2)
                groups = re.findall(r"([A-Za-z]+)(\d+(?:\.\d+)?)", token)
                if groups:
                    for g in groups:
                        normalized.append("." + g[0].upper() + g[1])
                else:
                    normalized.append("." + token.upper())
                continue

            # Merge year + single-letter suffix if split (e.g., "1983" followed by "b")
            if (
                re.fullmatch(r"\d{4}", token)
                and normalized
                and re.fullmatch(r"[A-Za-z]", normalized[-1])
            ):
                normalized[-1] = normalized[-1] + token
                continue

            # default: keep token
            normalized.append(token)

        # Post-process normalized tokens: casing rules
        out_lines = []
        for p in normalized:
            if re.fullmatch(r"\d{4}[A-Za-z]?", p):
                out_lines.append(p)
            elif re.fullmatch(r"v\.\d+|c\.\d+", p, re.IGNORECASE):
                out_lines.append(p.lower())
            elif p.startswith("."):
                # make letters uppercase in cutters
                m = re.match(r"\.([A-Za-z]+)(\d*(?:\.\d+)?)", p)
                if m:
                    out_lines.append(f".{m.group(1).upper()}{m.group(2)}")
                else:
                    out_lines.append(p)
            else:
                out_lines.append(p.upper())

        final_lines = parts + out_lines
        return "\n".join(final_lines)

    @staticmethod
    def _text_to_image(text):
        """
        Create a 306x306 px image with centered, left-aligned text.

        Args:
            text (str): Text string containing newlines

        Returns:
            PIL.Image: Image object with the rendered text
        """
        # Create a white image
        img = Image.new("RGB", (306, 306), color="white")
        draw = ImageDraw.Draw(img)

        # Use monospace font for Linux
        try:
            font = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 45
            )
        except:
            font = ImageFont.load_default()

        # Split text into lines
        lines = text.split("\n")

        # Calculate text dimensions for centering
        line_heights = []
        line_widths = []

        for line in lines:
            # Use a space to get proper height for blank lines
            text_to_measure = line if line else " "
            bbox = draw.textbbox((0, 0), text_to_measure, font=font)
            line_widths.append(bbox[2] - bbox[0] if line else 0)
            line_heights.append(bbox[3] - bbox[1])

        # Calculate total text block height
        line_spacing = 5
        total_height = sum(line_heights) + line_spacing * (len(lines) - 1)

        # Find the maximum line width for left alignment
        max_width = max(line_widths) if line_widths else 0

        # Calculate starting y position to center the text block vertically
        y = (306 - total_height) // 2

        # Calculate starting x position to center the text block horizontally
        x = (306 - max_width) // 2

        # Draw each line
        for i, line in enumerate(lines):
            draw.text((x, y), line, fill="black", font=font)
            y += line_heights[i] + line_spacing

        return img

    @staticmethod
    def prompt_confirm(msg: str, default: bool = None) -> bool:
        """
        prompt_confirm Make a confirmation prompt

        :param msg: Message
        :type msg: str
        :param default: Default value, defaults to None
        :type default: bool, optional
        :return: Confirm?
        :rtype: bool
        """

        if default:
            return input(f"{msg} [Y/n]: ").lower() in ["n", "no"]

        return input(f"{msg} [y/N]: ").lower() in ["y", "yes"]

    def _print_label(self, book: Book) -> None:
        print("Not implemented")

    def _manual_mode(self, book: Book = None) -> None:
        book = Book.from_book(book)

        if book.title is None:
            book.title = input("Enter book title: ")

        if book.isbn is None:
            book.isbn = input("Enter book ISBN: ").replace("-", "")

        if book.ident is None:
            book.ident = input("Enter book LOC ident: ")

        if book.authors is None:
            book.authors = input("Enter book authors: ")

        print(book)

    def print(self, image: Image) -> None:
        instructions = convert(BrotherQLRaster(self._model), [image], label="29")
        send(
            instructions=instructions,
            printer_identifier=self._printer,
            backend_identifier="linux_kernel",
        )

    def prompt_book(self, rapid: bool = False) -> bool:
        """
        prompt_book Prompt the user for a book

        :param rapid: Is rapid mode in use, defaults to False
        :type rapid: bool, optional
        :return: Should the program exit
        :rtype: bool
        """

        isbn = input("Enter ISBN: ").replace("-", "")

        if isbn == "quit":
            return True

        book = Generator._query_book(isbn)
        if book is None:
            beep()
            print("Book not found")
            if not rapid and Generator.prompt_confirm("Enter manual mode?"):
                self._manual_mode()
            return False

        print(book)

        if book.ident is None or book.ident == "":
            beep(2)
            print("Could not find LOC identifier")
            if not rapid and Generator.prompt_confirm("Enter manual mode?"):
                self._manual_mode(book)
            return False

        if not rapid and Generator.prompt_confirm("Is this correct?", True):
            return False

        if len(book.ident.replace(" ", "")) < 6 and not Generator.prompt_confirm(
            "Warning: Short LOC. Continue?"
        ):
            return False

        if not has_valid_year_at_end(book.ident) and book.year is not None:
            book.ident += " " + book.year[-4:]

        uid = Generator.store_book(book)
        label_text = f"AMH {uid:>04X}\n\n" + Generator._format_ident(book.ident)
        label = Generator._text_to_image(label_text)
        label.save("label.png")
        self.print(label)


def main() -> None:
    print("Welcome to the ISBN label printer")

    generator = Generator("QL-570", "file:///dev/usb/lp0")

    rapid = Generator.prompt_confirm("Prompt for manual mode on failure?", True)

    should_exit = False
    while not should_exit:
        should_exit = generator.prompt_book(rapid)


if __name__ == "__main__":
    main()
