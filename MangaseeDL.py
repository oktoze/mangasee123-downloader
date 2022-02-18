import asyncio
import json
import os
import re
import sys
from typing import Iterable

import aiofiles
import aiohttp
import requests

MANGASEE123HOST = "https://mangasee123.com"


def remove_leading_zeros(num: str) -> str:
    """
    Remove leading zeros from a string.
    """
    num = str(num)
    first_non_zero_index = 0

    for i, c in enumerate(num):
        if c != "0":
            first_non_zero_index = i
            break

    return num[first_non_zero_index:]


def add_leading_zeros(num: str, total_len: int) -> str:
    """
    Add leading zeros to a string to reach the specified length.
    """
    num = str(num)
    needed_zeros = total_len - len(num)

    if needed_zeros > 0:
        return "0" * needed_zeros + num

    return num


def get_chapter_first_page_url(manga_name: str, chapter: str, page: str):
    """
    Get mangasee123 reader url for a specific manga/chapter/page

    Boch chapter and page should be without leading zeros
    """
    return (
        f"{MANGASEE123HOST}/read-online/{manga_name}-chapter-{chapter}-page-{page}.html"
    )


def get_page_image_url(host, name, chapter, page):
    """
    Get hosted image url for a specific manga page
    """

    chapter = add_leading_zeros(chapter, 4)
    page = add_leading_zeros(page, 3)
    return f"https://{host}/manga/{name}/{chapter}-{page}.png"


def get_manga_details(name):
    """
    Get details for a manga from Mangasee123.
    Details include available chapters and number of pages in each chapter
    """
    url = get_chapter_first_page_url(name, "1", "1")

    resp = requests.get(url)
    content = resp.content.decode("utf-8")

    chapter_details_pattern = re.compile("vm.CHAPTERS = (.*);")
    chapter_details_str = chapter_details_pattern.search(content).groups()[0]
    chapter_details_list = json.loads(chapter_details_str)

    chapter_details_dict = {}
    for chapter_detail in chapter_details_list:
        chapter_details_dict[
            int(remove_leading_zeros(chapter_detail["Chapter"][1:-1]))
        ] = chapter_detail

    return chapter_details_dict


async def get_chapter_download_and_save_data(
    session, name: str, chapter: int, pages: int
) -> list:
    """
    Specify the url and save path for each page of a chapter
    """
    data = []

    url = get_chapter_first_page_url(name, chapter, 1)

    resp = await session.request(method="GET", url=url)
    content = await resp.text()
    host_pattern = re.compile('vm.CurPathName = "(.*)";')
    host = host_pattern.search(content).groups()[0]

    for page in range(1, int(pages) + 1):
        page = add_leading_zeros(page, 3)
        download_url = get_page_image_url(host, name, chapter, page)
        save_path = os.path.join(str(name), str(chapter), f"{page}.png")

        data.append({"download_url": download_url, "save_path": save_path})

    return data


async def download_and_save_chapter(session: aiohttp.ClientSession, name, chapter, pages):
    """
    Asynchronously download and save a page (skip if file exists)
    """
    try:
        print(f"Started downloading chpater {chapter}...")
        data = await get_chapter_download_and_save_data(session, name, chapter, pages)

        for d in data:
            download_url = d["download_url"]
            save_path = d["save_path"]

            if os.path.isfile(save_path):
                continue

            resp = await session.request(method="GET", url=download_url)

            async with aiofiles.open(save_path, "wb") as f:
                await f.write(await resp.read())
        print(f"Finished downloading chpater {chapter}...")
    except asyncio.TimeoutError:
        print(f"Timeout in downloading chapter {chapter}!")


async def download_chapters(name: str, chapter_details: Iterable):
    """
    Main couroutine for downloading chapters
    """
    if os.path.isfile(name):
        raise FileExistsError

    if not os.path.exists(name):
        os.mkdir(name)

    async with aiohttp.ClientSession() as session:
        print("Fetching requested chapter details...")

        coroutines = []
        for ch_detail in chapter_details:
            chapter = ch_detail["Chapter"][1:-1]
            pages = int(ch_detail["Page"])

            if not os.path.isdir(os.path.join(name, chapter)):
                os.mkdir(os.path.join(name, chapter))

            coroutines.append(
                download_and_save_chapter(session, name, chapter, pages),
            )

        print(f"Downloading requested chapters...")
        await asyncio.gather(*coroutines)
        print("Download completed!")


if __name__ == "__main__":
    help = """
    Usage: python mangasee123-downloader.py MANGA_NAME [CHAPTER_START [CHAPTER_END]]

    Download mangas from https://mangasee123.com/

    Note: MANGA_NAME is case insensitives. If it contains spaces, you can place hyphen ("-") instead of spaces or just put the name into quoutations.
    Note: Downloaded images will be placed into {working directory}/{manga name}/{chapter number}/{page number}

    Options:
        If nothing other than MANGA_NAME is provided, the script tries to download all chapters.
            Example: python downloader.py Vagabond

        If only CHAPTER_START is provided, only that chapter is downloaded.
            Example: $ python downloader.py one-piece 10
            will download chapter 10

        If CHAPTER_START and CHAPTER_END are both provided, the script tries to download CHAPTER_START to CHAPTER_END
            Example: $ python downloader.py Diamond-Is-Unbreakable 10 20
            will download chapter 10 through 20
    """
    if len(sys.argv) == 1:
        print(help)
        sys.exit()

    name = "-".join(sys.argv[1].title().split())

    try:
        chapters_dict = get_manga_details(name)
        print(f"Fetched details for {name}...")
    except AttributeError:
        print(f"Could not get info for {name} from http://mangasee123.com")
        sys.exit()
    except requests.exceptions.ConnectionError:
        print(f"Could not connect to http://mangasee123.com")
        sys.exit()

    min_chapter = min(chapters_dict.keys())
    max_chapter = max(chapters_dict.keys())
    non_available_chapters = list(
        set(range(min_chapter, max_chapter + 1)) - set(chapters_dict.keys())
    )

    try:
        if len(sys.argv) == 2:
            target_chapters = chapters_dict.values()
        elif len(sys.argv) == 3:
            ch = int(sys.argv[2])
            target_chapters = [chapters_dict[ch]]
        elif len(sys.argv) == 4:
            ch_start = int(sys.argv[2])
            ch_end = int(sys.argv[3])

            target_chapters = []
            for ch in range(ch_start, ch_end + 1):
                chapter = chapters_dict.get(ch)
                if not chapter:
                    print(f"Chapter {ch} is not available, skipping...")
                else:
                    target_chapters.append(chapter)
        else:
            print(help)
            sys.exit()
    except ValueError:
        print("Could not parse input!")
        print(help)
        sys.exit()
    except KeyError:
        print("Could not find specified chapter(s)!")
        print(f"Available chapter: {min_chapter}-{max_chapter}")
        print(f"Not available chapters: {non_available_chapters}")
        sys.exit()

    try:
        asyncio.run(download_chapters(name, target_chapters))
    except FileExistsError:
        print(
            f"Could not create directory {name}, It appears that a file with that name exists!"
        )
        sys.exit()
