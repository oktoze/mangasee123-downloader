#!/usr/bin/env python3

import asyncio
import argparse
from http.client import HTTPConnection
import json
import logging
import os
import pprint
import re
import sys
from typing import Iterable

import aiofiles
import aiohttp
import requests

MANGASEE123HOST = "https://mangasee123.com"

# Cloudflare seemingly blocks connections with the requests UA. :shrug:
# You can probably set it to anything reasonable
USERAGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:101.0) Gecko/20100101 Firefox/101.0"

logging.basicConfig()
LOGGER = logging.getLogger()

def add_verbosity():
    """
    Turn on quite a bit of verbose logging to figure out why downloads are
    failing. You don't want this normally
    """

    HTTPConnection.debuglevel = 1
    LOGGER.setLevel(logging.DEBUG)
    requests_log = logging.getLogger("requests.packages.urllib3")
    requests_log.setLevel(logging.DEBUG)
    requests_log.propagate = True


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


def get_referer_for_name(manga_name: str):
    """
    Get mangasee123 page that would link to the chapter first_page_url
    """
    return (
        f"{MANGASEE123HOST}/manga/{manga_name}"
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
    referer = get_referer_for_name(name)

    resp = requests.get(url, timeout=30, headers={
        'referer': referer, 'User-Agent': USERAGENT}
                        )
    content = resp.content.decode("utf-8")

    chapter_details_pattern = re.compile("vm.CHAPTERS = (.*);")
    chapter_details_str = chapter_details_pattern.search(content).groups()[0]
    chapter_details_list = json.loads(chapter_details_str)
    logging.getLogger().debug("First page chapter details: %s",
                              pprint.pformat(chapter_details_list))

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


async def download_and_save_chapter(
    session: aiohttp.ClientSession, name, chapter, pages
):
    """
    Asynchronously download and save a page (skip if file exists)
    """
    try:
        print(f"Started downloading chapter {chapter}...")
        data = await get_chapter_download_and_save_data(session, name, chapter, pages)

        for d in data:
            download_url = d["download_url"]
            save_path = d["save_path"]

            if os.path.isfile(save_path):
                continue

            resp = await session.request(method="GET", url=download_url)

            async with aiofiles.open(save_path, "wb") as f:
                await f.write(await resp.read())
        print(f"Finished downloading chapter {chapter}...")
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
    parser = argparse.ArgumentParser()
    parser.add_argument("manga_name")
    parser.add_argument("chapter_start", nargs="?", type=int)
    parser.add_argument("chapter_end", nargs="?", type=int)
    parser.add_argument(
        "-l", "--limit", help="Limit maximum simultaneous chapter downloads", type=int
    )
    parser.add_argument("-v", "--verbose", help="Add debugging output",
                            action="store_true")

    try:
        args = parser.parse_args()
    except SystemExit:
        print(help)
        sys.exit()

    if args.verbose:
        add_verbosity()

    name = "-".join(args.manga_name.title().split())

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
        if not args.chapter_start:
            target_chapters = chapters_dict.values()
        elif args.chapter_start and not args.chapter_end:
            ch = args.chapter_start
            target_chapters = [chapters_dict[ch]]
        else:
            ch_start = args.chapter_start
            ch_end = args.chapter_end

            target_chapters = []
            for ch in range(ch_start, ch_end + 1):
                chapter = chapters_dict.get(ch)
                if not chapter:
                    print(f"Chapter {ch} is not available, skipping...")
                else:
                    target_chapters.append(chapter)

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
        limit = args.limit or len(target_chapters)

        for i in range(0, len(target_chapters), limit):
            asyncio.run(download_chapters(name, target_chapters[i : i + limit]))
    except FileExistsError:
        print(
            f"Could not create directory {name}, It appears that a file with that name exists!"
        )
        sys.exit()
