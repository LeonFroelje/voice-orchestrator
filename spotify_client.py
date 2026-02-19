import spotipy
from typing import Optional
from spotipy.oauth2 import SpotifyOAuth


class SpotifyClient:
    def __init__(self, client_id, client_secret, redirect_uri):
        scope = "user-modify-playback-state user-read-playback-state"
        self.sp = spotipy.Spotify(
            auth_manager=SpotifyOAuth(
                client_id=client_id,
                client_secret=client_secret,
                redirect_uri=redirect_uri,
                scope=scope,
            )
        )

    def search_and_play(
        self, query: str, device_name: Optional[str] = None, search_type: str = "track"
    ):
        device_id = None

        # 1. Only find Device ID if a name was provided
        if device_name:
            devices = self.sp.devices()
            device_id = next(
                (
                    d["id"]
                    for d in devices["devices"]
                    if d["name"].lower() == device_name.lower()
                ),
                None,
            )
            if not device_id:
                return False, f"Gerät '{device_name}' wurde nicht gefunden."

        # 2. Search for the content
        results = self.sp.search(q=query, limit=1, type=search_type)
        items = results.get(search_type + "s", {}).get("items", [])

        if not items:
            return False, f"Ich konnte kein {search_type} für '{query}' finden."

        uri = items[0]["uri"]
        name = items[0]["name"]

        # 3. Start Playback (device_id=None defaults to active device)
        try:
            if search_type == "track":
                self.sp.start_playback(device_id=device_id, uris=[uri])
            else:
                self.sp.start_playback(device_id=device_id, context_uri=uri)

            target = device_name if device_name else "dem aktiven Gerät"
            return True, f"Spiele {name} auf {target}."
        except Exception as e:
            return False, f"Fehler bei der Wiedergabe: {str(e)}"

    def play_music(self, device_name: str, context_uri: Optional[str] = None):
        # Find the device ID by name (e.g., your raspotify satellite name)
        devices = self.sp.devices()
        device_id = next(
            (
                d["id"]
                for d in devices["devices"]
                if d["name"].lower() == device_name.lower()
            ),
            None,
        )

        if device_id:
            self.sp.start_playback(device_id=device_id, context_uri=context_uri)
            return True
        return False
