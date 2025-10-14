from dataclasses import dataclass
import requests
from time import sleep


def beep(count=1):
    for i in range(count):
        print("\a", end="", flush=True)
        if i < count - 1:
            sleep(0.25)


@dataclass
class Book:
    isbn: str | None
    ident: str | None
    title: str | None
    authors: str | None

    def __str__(self) -> str:
        return f"\nTitle: {self.title}\nISBN: {self.isbn}\nAuthors: {self.authors}\nLOC: {self.ident}\n"

    @staticmethod
    def from_book(book: "Book") -> "Book":
        if book is None:
            return Book(None, None, None, None)

        return Book(
            isbn=book.isbn,
            ident=book.ident if book.ident != "" else None,
            title=book.title,
            authors=book.authors,
        )


class Generator:
    def __init__(self, printer_mac: str):
        self._printer_mac = printer_mac

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
            ident = book["classifications"]["lc_classifications"][0]
        else:
            ident = None

        return Book(
            isbn=isbn,
            ident=ident,
            title=book["title"],
            authors=" and ".join(map(lambda x: x["name"], book["authors"])),
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
    def _format_ident(ident: str) -> str:
        letters = ""
        code = ""
        if ident[1].isalpha():
            letters = ident[:2]
            code = ident[2:]
        else:
            letters = ident[:1]
            code = ident[1:]

        return f"{letters}\n{"\n".join(code.split("."))}"

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

        uid = Generator.store_book(book)
        print("Printing label:")
        print("AMH")
        print(f"{uid:>04}")
        print(Generator._format_ident(book.ident))


def format_mac(mac):
    # Ensure the input has exactly 12 hexadecimal characters
    mac = mac.strip().replace(":", "").replace("-", "").upper()
    if len(mac) != 12 or not all(c in "0123456789ABCDEF" for c in mac):
        raise ValueError("Invalid MAC address format")

    # Group into pairs and join with colons
    return ":".join(mac[i : i + 2] for i in range(0, 12, 2))


def main() -> None:
    print("Welcome to the ISBN label printer")

    generator = Generator(format_mac(input("Enter label printer MAC: ")))

    rapid = Generator.prompt_confirm("Prompt for manual mode on failure?", True)

    should_exit = False
    while not should_exit:
        should_exit = generator.prompt_book(rapid)


if __name__ == "__main__":
    main()
