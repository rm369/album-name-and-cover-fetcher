import argparse
import os
import sys
import time
import requests
import mutagen
import yaml
from mutagen.id3 import ID3, TALB, TPE1, TIT2, APIC, ID3NoHeaderError
from requests.auth import HTTPBasicAuth

# Spotify API credentials
spotify_credentials_file = "spotify_credentials.yaml"
spotify_credentials = {}
access_token = None

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
            print(f"Too many requests, sleeping {retry_after_time} seconds...")
            for i in range(int(retry_after_time), 0, -1):
                sys.stdout.write("\r")
                sys.stdout.write(str(i) + ' ')
                sys.stdout.flush()
                time.sleep(1)
                response = requests.get(url, headers=headers, params=params) # retry
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
        "limit": 1,
    }
    response = get_from_spotify(url, params)
    response_data = response.json()

    # Debugging: Print the response data
    print("Spotify API response:", response_data)

    if 'tracks' in response_data and response_data['tracks']['items']:
        track = response_data['tracks']['items'][0]
        album_title = track['album']['name']
        album_id = track['album']['id']
        print(f"Found album: {album_title} with ID: {album_id} for artist: {artist} and title: {title}")
        return album_title, album_id
    else:
        print(f"No suitable album found for artist: {artist} and title: {title}. Album name will not be changed.")
        return None, None

def get_cover_art_from_spotify(album_id):
    if not album_id:
        return None
    album_url = f"https://api.spotify.com/v1/albums/{album_id}"
    response = get_from_spotify(album_url, {})
    response_data = response.json()
    if response_data.get('images'):
        cover_art_url = response_data['images'][0]['url']
        cover_art_data = requests.get(cover_art_url).content
        print(f"Found cover art for album ID: {album_id}")
        return cover_art_data
    else:
        print(f"No cover art found for album ID: {album_id}")
        return None

def update_file_metadata(file_path):
    print(f"Processing file: {file_path}")
    try:
        audio = ID3(file_path)
    except ID3NoHeaderError:
        print(f"No ID3 header found for file: {file_path}. Skipping.")
        return
    
    artist_frame = audio.get('TPE1', None)
    title_frame = audio.get('TIT2', None)
    if not artist_frame or not title_frame:
        print("Artist or title tag not found. Skipping file.")
        return
    
    artist = artist_frame.text[0] if artist_frame and isinstance(artist_frame, mutagen.id3.TextFrame) else None
    title = title_frame.text[0] if title_frame and isinstance(title_frame, mutagen.id3.TextFrame) else None
    
    if not artist or not title:
        print("Artist or title tag not correctly formatted. Skipping file.")
        return
    
    print(f"Artist: {artist}, Title: {title}")
    
    album_title, album_id = get_album_info_from_spotify(artist, title)

    updated = False
    if album_title and album_title != audio.get('TALB', None):
        audio.delall('TALB')
        audio.add(TALB(encoding=3, text=album_title))
        updated = True

    cover_art_data_old = audio.get('APIC:Cover', None)
    if album_id: # and not cover_art_data_old:
        cover_art_data = get_cover_art_from_spotify(album_id)
        if cover_art_data != cover_art_data_old:
            audio.delall('APIC')
            audio.add(APIC(
                encoding=3,
                mime='image/jpeg',
                type=3, desc='Cover',
                data=cover_art_data
            ))
            updated = True

    if updated:
        audio.save(file_path)
        print(f"Updated file: {file_path} with album: {album_title if album_title else 'No change'}")

def process_folder(folder_path):
    for root, _, files in os.walk(folder_path):
        for file in files:
            if file.endswith(('.mp3', '.flac', '.ogg', '.m4a')):
                file_path = os.path.join(root, file)
                update_file_metadata(file_path)

if __name__ == "__main__":

    with open(spotify_credentials_file, "r") as f:
        spotify_credentials = yaml.safe_load(f)
    get_spotify_access_token()

    parser = argparse.ArgumentParser()
    parser.add_argument("path")
    args = parser.parse_args()
    folder_path = args.path

    process_folder(folder_path)
