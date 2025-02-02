#!/usr/bin/env python3

import logging
import os
import pickle
import random
import re
import sys
import time
import traceback
from datetime import datetime as dt
from subprocess import run

import feedparser
import html2markdown
from fuzzywuzzy import process
from telegram import Update, ParseMode
from telegram.ext import Updater, CommandHandler, CallbackContext

# Read bot token from environment
TOKEN = os.environ['MIA_TG_TOKEN']
CHAT_IDS = os.environ['MIA_TG_CHATID'].split(',')
URL = f"https://api.telegram.org/bot{TOKEN}/"
DUMP = os.getenv('MIA_DUMP', '')
DIRNAME = os.path.dirname(os.path.realpath(__file__))
MINKORREKT_RSS = 'http://minkorrekt.de/feed/mp3'


# Setup logging
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
log_format = logging.Formatter('%(asctime)s %(levelname)s - %(message)s')
log_handler = logging.StreamHandler(sys.stdout)
log_handler.setFormatter(log_format)
log_handler.setLevel(logging.INFO)
logger.addHandler(log_handler)


class PodcastFeed:
    """Represents the parsed and cached podcast RSS feed"""

    def __init__(self, url: str, max_age: int = 3600, dump: str = ''):
        """
        :param url: URL of the feed to be parsed.
        :param max_age: Time in seconds how long the parsed feed is consider valid.
                        The feed will be refreshed automatically on the first access
                        after max_age is passed.
        :param dump: Allows local storage of the feed. If a valid file path, the feed
                     object will be stored there after refresh, and reloaded on
                     re-initialization. Primarily intended to speed loading time for debugging.
        """
        self.url = url
        self.max_age = max_age
        self.dump = dump

        if dump and os.path.isfile(dump):
            try:
                with open(dump, 'rb') as f:
                    self.last_updated, self.feed = pickle.load(f)
                logger.info('Reloaded dumped feed')
            except Exception as exc:
                logger.info(f'{exc!r}\n{traceback.format_exc()}')
                logger.info('Failed loding dumped feed. Falling back to download.')
                self._get_feed()
        else:
            logger.info('Getting feed')
            self._get_feed()

    def _get_feed(self):
        self.feed = feedparser.parse(self.url)
        self.last_updated = time.time()
        logger.info('Done parsing feed')
        if self.dump:
            with open(self.dump, 'wb') as f:
                pickle.dump((self.last_updated, self.feed), f)

    def refresh(self):
        if self.last_updated + self.max_age < time.time():
            logger.info('Refreshing feed')
            self._get_feed()

    def check_new_episode(self, max_age=3600):
        latest_episode = self.latest_episode
        episode_release = dt.fromtimestamp(time.mktime(latest_episode['published_parsed']))
        if (dt.now() - episode_release).total_seconds() < max_age:
            return latest_episode
        return False

    @property
    def latest_episode(self):
        self.refresh()
        return self.feed['items'][0]

    @property
    def episode_titles(self):
        self.refresh()
        return [i.title for i in self.feed['items']]


mi_feed = PodcastFeed(url=MINKORREKT_RSS, dump=DUMP)


def tg_broadcast(text, escape_chars=['!', '#', '-']):
    """Sends the message `text` to all CHAT_IDS."""
    for c in escape_chars:
        text = re.sub(f'(?<!\\\\){c}', f'\\{c}', text)
    for chat_id in CHAT_IDS:
        bot.send_message(chat_id=chat_id,
                         text=text,
                         parse_mode=ParseMode.MARKDOWN_V2)


def check_minkorrekt(max_age=3600):
    new_episode = mi_feed.check_new_episode(max_age=max_age)
    if new_episode:
        tg_broadcast(f'*{new_episode.title}*\n'
                     'Eine neue Folge Methodisch inkorrekt ist erschienen\\!\n'
                     f'[Jetzt anhören]({new_episode.link})')


def check_youtube(max_age=3600):
    YOUTUBE_RSS = 'https://www.youtube.com/feeds/videos.xml?channel_id=UCa8qyXCS-FTs0fHD6HJeyiw'
    yt_feed = feedparser.parse(YOUTUBE_RSS)
    newest_episode = yt_feed['items'][0]
    episode_release = dt.fromtimestamp(time.mktime(newest_episode['published_parsed']))
    if (dt.now() - episode_release).total_seconds() < max_age:
        tg_broadcast(f'*{newest_episode.title}*\n'
                     'Eine neues Youtube Video ist erschienen!\n'
                     f'[Jetzt ansehen]({newest_episode.link})')


def feed_loop():
    while True:
        check_minkorrekt(3600)
        check_youtube(3600)
        time.sleep(3595)


def latest_episode(update: Update, context: CallbackContext) -> None:
    latest_episode = mi_feed.latest_episode
    episode_release = dt.fromtimestamp(time.mktime(latest_episode['published_parsed'])).date()
    datum = episode_release.strftime('%d.%m.%Y')
    text = (f'Die letzte Episode ist *{latest_episode.title}* vom {datum}.\n'
            f'[Jetzt anhören]({latest_episode.link})')
    update.message.reply_text(text, quote=False, parse_mode=ParseMode.MARKDOWN_V2)


def cookie(update: Update, context: CallbackContext) -> None:
    text = random.choice(mi_feed.episode_titles)
    update.message.reply_text(f'\U0001F36A {text} \U0001F36A', quote=False)


def crowsay(update: Update, context: CallbackContext) -> None:
    i = update.message.text.find(' ')
    if i > 0:
        text = update.message.text[i+1:]
    else:
        r = run('fortune', capture_output=True, encoding='utf-8')
        text = r.stdout

    crowfile = os.path.join(DIRNAME, 'crow.cow')
    r = run(['cowsay', '-f', crowfile, text],
            capture_output=True, encoding='utf-8')
    text = r.stdout
    update.message.reply_text(f'```\n{text}\n```', quote=False, parse_mode=ParseMode.MARKDOWN_V2)


def fuzzy_topic_search(update: Update, context: CallbackContext) -> None:
    i = update.message.text.find(' ')
    if i > 0:
        search_term = update.message.text[i+1:]
    topics_all_episodes = [[i.title, i.content[0].value.replace(
        "<!-- /wp:paragraph -->", "").replace("<!-- wp:paragraph -->", "")
                            ] for i in mi_feed.feed.entries]
    ratios = process.extract(search_term, topics_all_episodes)
    episodes = [ratio[0][0] for ratio in ratios[:3]]
    text = "Die besten 3 Treffer sind die Episoden:\n" + "\n".join(episodes)
    update.message.reply_text(text, quote=False, parse_mode=ParseMode.MARKDOWN)


def topics_of_episode(update: Update, context: CallbackContext) -> None:

    regex_pattern_episode_number = r"(?:Minkorrekt(?:\ Folge)?\ |Mi)(\d+\w?)"

    # List of titles+description per episode
    episode_numbers = [re.match(regex_pattern_episode_number, entry.title)[1] if not re.match(
        regex_pattern_episode_number, entry.title)
                       is None else None for entry in mi_feed.feed.entries]
    topics_all_episodes = [[i.title, i.content[0].value.replace(
        "<!-- /wp:paragraph -->", "").replace("<!-- wp:paragraph -->", "")
                            ] for i in mi_feed.feed.entries]

    i = update.message.text.find(' ')
    if i > 0:
        requested_episode_number = update.message.text[i+1:]
        if requested_episode_number == '101':
            import pdb
            pdb.set_trace()
    else:
        text = "Bitte eine Episodennummer beim Aufruf mit angeben."
        update.message.reply_text(text, quote=False, parse_mode=ParseMode.MARKDOWN)
        return None
    if requested_episode_number == '12':
        target_episode_index = episode_numbers.index("12a")
        # Get the entries for 12a and 12b and reverse the order so they're correctly displayed
        requested_episode_topics = topics_all_episodes[
            target_episode_index:target_episode_index+1][::-1]
    else:
        try:
            target_episode_index = episode_numbers.index(requested_episode_number)
        except Exception:
            text = f"""Nicht gefunden.\nEpisode {requested_episode_number}
            gab es möglicherweise nicht."""
            update.message.reply_text(text, quote=False, parse_mode=ParseMode.MARKDOWN)
            return None
        requested_episode_topics = topics_all_episodes[target_episode_index]

    requested_episode_topics = " ".join(requested_episode_topics)
    topic_start_points = [m.start() for m in re.finditer(
        "Thema [1, 2, 3, 4]", requested_episode_topics)]
    topic_end_points = []
    for start in topic_start_points:
        topic_end_points.append(start + requested_episode_topics[start:].find('\n'))
    if 0 == len(topic_start_points):
        text = "Themen nicht gefunden.\nWahrscheinlich Nobelpreis/Jahresrückblick-Folge"
        update.message.reply_text(text, quote=False, parse_mode=ParseMode.MARKDOWN)
        return None
    topics = [html2markdown.convert(requested_episode_topics[start:end]) for start, end in zip(
        topic_start_points, topic_end_points)]
    topics_text = "\n".join(topics)

    if requested_episode_number == '12':
        episode_title = "12a Du wirst wieder angerufen! & 12b Previously (on) Lost"
    else:
        episode_title = topics_all_episodes[target_episode_index][0]
    text = f"Die Themen von Folge {episode_title} sind:\n{topics_text}"
    update.message.reply_text(text, quote=False, parse_mode=ParseMode.MARKDOWN)


updater = Updater(TOKEN)
bot = updater.bot

updater.dispatcher.add_handler(CommandHandler('findeStichwort', fuzzy_topic_search))
updater.dispatcher.add_handler(CommandHandler('themenVonFolge', topics_of_episode))
updater.dispatcher.add_handler(CommandHandler('letzteEpisode', latest_episode))
updater.dispatcher.add_handler(CommandHandler('keks', cookie))
updater.dispatcher.add_handler(CommandHandler('crowsay', crowsay))


if __name__ == '__main__':
    updater.start_polling()
    feed_loop()
    # updater.idle()
