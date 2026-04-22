import time

from main import paste_text


def main() -> None:
    print("Autotyper will paste in 5 seconds...")
    time.sleep(5)
    paste_text()


if __name__ == "__main__":
    main()
