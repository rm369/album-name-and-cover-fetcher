#!/usr/bin/env python3
import argparse
import datetime
import logging
import os
import sys
import time
import requests
import mutagen
import yaml
from mutagen.id3 import ID3, TALB, TPE1, TIT2, APIC, ID3NoHeaderError
from requests.auth import HTTPBasicAuth
from dateutil.parser import parse

# Spotify API credentials
spotify_credentials_file = "spotify_credentials.yaml"
spotify_credentials = {}
access_token = None

log_filename = "log/album_info_art_fetcher.log"

def get_spotify_access_token():
    global access_token
    url = "https://accounts.spotify.com/api/token"
    headers = {
        "Authorization": "Basic " + (spotify_credentials['id'] + ":" + spotify_credentials['secret']).encode("ascii").decode("ascii"),
    }
    data = {
        "grant_type": "client_credentials",
    }
    response = requests.post(url, headers=headers, data=data, auth=HTTPBasicAuth(spotify_credentials['id'], spotify_credentials['secret']))
    response_data = response.json()
    if "access_token" in response_data:
        access_token = response_data["access_token"]
    else:
        raise Exception(f"Error obtaining access token: {response_data}")
    return None


def get_from_spotify(url, params):
    global access_token
    retry = True
    while retry:
        headers = {
            "Authorization": f"Bearer {access_token}",
        }
        response = requests.get(url, headers=headers, params=params)
        if response.status_code == 429: # 'Too many requests'
            retry_after_time = response.headers['retry-after']
            logging.info(f"Too many requests, sleeping {retry_after_time} seconds...")
            for i in range(int(retry_after_time) + 2, 0, -1):
                sys.stdout.write("\r")
                sys.stdout.write(str(i) + ' ')
                sys.stdout.flush()
                time.sleep(1)
        elif response.status_code == 401: # 'The access token expired'
            get_spotify_access_token()
        else:
            # retry = False
            return response


def get_album_info_from_spotify(artist, title):
    url = "https://api.spotify.com/v1/search"
    params = {
        "q": f"artist:{artist} track:{title}",
        "type": "track",
        "limit": 20,
    }
    response = get_from_spotify(url, params)
    response_data = response.json()
    # logging.debug("Spotify API response:", response_data)

    a = {} # album data found - use first released with cover images, otherwise first released
    if 'tracks' in response_data and 'items' in response_data['tracks']:
        for track in response_data['tracks']['items']:
            if 'album' in track:
                album = track['album']
                if 'release_date' in album:
                    try:
                        release_date = parse(album['release_date'])
                    except ValueError:
                        release_date = datetime.datetime.now()
                else:
                    release_date = datetime.datetime.now()
                if 'images' in album and len(album['images']) > 0:
                    cover_url = album['images'][0]['url'] # biggest is first
                else:
                    cover_url = None
                if not a \
                    or (release_date < a['release_date']
                        and (cover_url or not a.get('cover_url'))):
                    # if no album found yet or older than stored album and cover found or no cover also in stored album
                    a['release_date'] = release_date
                    a['cover_url'] = cover_url
                    a['album_title'] = album['name']
                    a['album_id'] = album['id']
    return a


def update_file_metadata(file_path, cache):
    logging.info(f"Processing file: {file_path}")
    try:
        audio = ID3(file_path)
    except ID3NoHeaderError:
        logging.info(f"No ID3 header found for file: {file_path}. Skipping.")
        cache[file_path] = None
        return
    artist_frame = audio.get('TPE1', None)
    title_frame = audio.get('TIT2', None)
    artist = artist_frame.text[0] if artist_frame and isinstance(artist_frame, mutagen.id3.TextFrame) else None
    title = title_frame.text[0] if title_frame and isinstance(title_frame, mutagen.id3.TextFrame) else None
    if not artist or not title:
        logging.info("Artist or title tag not found in ID3. Skipping file.")
        cache[file_path] = {'artist': artist, 'title': title}
        return
    logging.info(f"Artist: {artist}, Title: {title}")
    
    a = get_album_info_from_spotify(artist, title)
    if a:
        logging.info(f"Found album: {a} for artist: {artist} and title: {title}")
    else:
        logging.info(f"No suitable album found for artist: {artist} and title: {title}. Album data will not be changed.")
        cache[file_path] = {'artist': artist, 'title': title}
        return

    updated = False
    album_tile_old = audio.get('TALB', None)
    if a['album_title'] != album_tile_old:
        logging.info(f"Replacing album tag {album_tile_old} with {a['album_title']}")
        audio.delall('TALB')
        audio.add(TALB(encoding=3, text=a['album_title']))
        updated = True

    cover_art_data_old = audio.get('APIC:Cover', None)
    if a['cover_url']: # and not cover_art_data_old:
        cache[file_path] = {'artist': artist, 'title': title, 'album': a['album_title'], 'cover_url': a['cover_url']}
        cover_art_data = requests.get(a['cover_url']).content
        if cover_art_data != cover_art_data_old:
            logging.info(f"Replacing album cover")
            audio.delall('APIC')
            audio.add(APIC(
                encoding=3,
                mime='image/jpeg',
                type=3, desc='Cover',
                data=cover_art_data
            ))
            updated = True
    else:
        cache[file_path] = {'artist': artist, 'title': title, 'album': a['album_title']}

    if updated:
        audio.save(file_path)


def process_folder(folder_path, cache):
    for root, _, files in os.walk(folder_path):
        for file in files:
            if file.endswith(('.mp3', '.flac', '.ogg', '.m4a')):
                file_path = os.path.join(root, file)
                if file_path not in cache or 'cover_url' not in cache[file_path]:
                    update_file_metadata(file_path, cache)


if __name__ == "__main__":

    with open(spotify_credentials_file, "r") as f:
        spotify_credentials = yaml.safe_load(f)

    parser = argparse.ArgumentParser()
    parser.add_argument("path",
                        help="root path")
    parser.add_argument("--cache",
                        help="name of cache yaml file")
    parser.add_argument("--missing",
                        help="name of created yaml file with all files for which no cover is found")
    parser.add_argument("--spotify_id",
                        help="Spotify ID (request App at https://developer.spotify.com/dashboard")
    parser.add_argument("--spotify_secret",
                        help="Spotify secret")
    args = parser.parse_args()

    logging.basicConfig(format="%(asctime)s: %(message)s",
                        level=logging.INFO,
                        datefmt="%Y-%m-%d %H:%M:%S",
                        handlers=[
                            logging.FileHandler(log_filename),
                            logging.StreamHandler()
                        ])

    if args.spotify_id:
        spotify_credentials['id'] = args.spotify_id
    if args.spotify_secret:
        spotify_credentials['secret'] = args.spotify_secret
    get_spotify_access_token()

    if args.cache:
        try:
            with open(args.cache, "r") as f:
                cache = yaml.safe_load(f)
        except Exception as e:
            logging.debug(f"Couldn't load cache file {args.cache}, starting with empty cache.")
            cache = {}
    else:
        cache = {}
    process_folder(args.path, cache)

    if args.cache:
        with open(args.cache, "w") as f:
            f.write(yaml.dump(cache, allow_unicode=True))

    if args.missing:
        missing = {d for d in cache if 'cover_url' not in d}
        with open(args.missing, "w") as f:
            f.write(yaml.dump(missing, allow_unicode=True))
