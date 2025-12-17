#!/usr/bin/env python3

import logging
import sys
from pathlib import Path
import Ice
import json
from datetime import datetime, timezone
import hashlib, secrets

Ice.loadSlice('-I{} spotifice_v2.ice'.format(Ice.getSliceDir()))
import Spotifice

logging.basicConfig(level=logging.INFO, format='%(name)s: %(message)s')
logger = logging.getLogger("MediaServer")

def _verify_password(password: str, salt: str, digest: str) -> bool:
    calc = hashlib.md5((password + salt).encode('utf-8')).hexdigest()
    return secrets.compare_digest(calc, digest)

def _parse_created_at(v):
    if isinstance(v, (int, float)): return int(v)
    if isinstance(v, str) and v.strip():
        try:
            dt = datetime.strptime(v.strip(), "%d-%m-%Y")
            return int(dt.replace(tzinfo=timezone.utc).timestamp())
        except: pass
    return int(datetime.now(timezone.utc).timestamp())

class SecureStreamManagerI(Spotifice.SecureStreamManager):
    def __init__(self, server_impl, username):
        self._server = server_impl
        self._username = username
        self._fh = None

    def open_stream(self, track_id, current=None):
        self.close_stream(current)
        info = self._server.get_track_info(track_id)
        path = self._server.media_dir / info.filename
        try:
            self._fh = open(path, "rb")
        except FileNotFoundError:
            raise Spotifice.IOError(item=track_id, reason="Fichero no encontrado")
        except Exception as e:
            raise Spotifice.IOError(item=track_id, reason=f"Error IO: {e}")

    def get_audio_chunk(self, chunk_size, current=None):
        if not self._fh: raise Spotifice.StreamError("No hay stream abierto")
        try:
            data = self._fh.read(chunk_size)
            return bytearray(data or b"")
        except Exception as e:
             raise Spotifice.StreamError(f"Error lectura: {e}")

    def close_stream(self, current=None):
        if self._fh:
            self._fh.close()
            self._fh = None

    def close(self, current=None):
        self.close_stream(current)
        self._server.remove_session(self._username)

class MediaServerI(Spotifice.MediaServer):
    def __init__(self, media_dir, playlists_dir, users_file: Path):
        self.media_dir = Path(media_dir)
        self.playlists_dir = Path(playlists_dir)
        self.tracks = {}
        self._playlists = {}
        self._sessions = {}
        self._users = {}
        
        self.load_media()
        self.load_playlists()
        self.load_users(users_file)

    def load_users(self, users_file):
        if users_file.exists():
            try:
                raw = json.loads(users_file.read_text(encoding='utf-8'))
                for k, v in raw.items():
                    self._users[k.strip()] = {sk.strip(): sv.strip() if isinstance(sv, str) else sv for sk, sv in v.items()}
                logger.info(f"Usuarios cargados: {len(self._users)}")
            except Exception as e:
                logger.error(f"Error cargando usuarios: {e}")

    def load_playlists(self):
        self._playlists.clear()
        self.playlists_dir.mkdir(parents=True, exist_ok=True)
        for f in sorted(self.playlists_dir.glob("*.playlist")):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                valid_ids = [tid for tid in data.get("track_ids", []) if tid in self.tracks]
                pl = Spotifice.Playlist(
                    id=data["id"],
                    name=data.get("name", "Sin nombre"),
                    description=data.get("description", ""),
                    owner=data.get("owner", "Sistema"),
                    created_at=_parse_created_at(data.get("created_at")),
                    track_ids=valid_ids
                )
                self._playlists[pl.id] = pl
            except Exception: pass
        logger.info(f"Playlists cargadas: {len(self._playlists)}")

    def load_media(self):
        self.tracks.clear()
        if not self.media_dir.exists(): return
        for f in sorted(self.media_dir.iterdir()):
            if f.is_file() and f.suffix.lower() == ".mp3":
                self.tracks[f.name] = Spotifice.TrackInfo(id=f.name, title=f.stem, filename=f.name)
        logger.info(f"Pistas indexadas: {len(self.tracks)}")

    def get_all_tracks(self, current=None): return list(self.tracks.values())
    
    def get_track_info(self, track_id, current=None): 
        if track_id not in self.tracks: raise Spotifice.TrackError(track_id, "No encontrada")
        return self.tracks[track_id]
        
    def get_all_playlists(self, current=None): return list(self._playlists.values())
    
    def get_playlist(self, playlist_id, current=None):
        if playlist_id not in self._playlists: raise Spotifice.PlaylistError(playlist_id, "No encontrada")
        return self._playlists[playlist_id]

    def authenticate(self, media_render, username, password, current=None):
        if not media_render: raise Spotifice.BadReference("Render invalido")
        if username not in self._users: raise Spotifice.AuthError("Credenciales invalidas", username)
        
        u = self._users[username]
        if not _verify_password(password, u["salt"], u["digest"]):
             raise Spotifice.AuthError("Credenciales invalidas", username)
        
        servant = SecureStreamManagerI(self, username)
        proxy = current.adapter.addWithUUID(servant)
        self._sessions[username] = servant
        logger.info(f"Login exitoso: {username}")
        return Spotifice.SecureStreamManagerPrx.uncheckedCast(proxy)

    def remove_session(self, username):
         self._sessions.pop(username, None)

def main(ic):
    props = ic.getProperties()
    server_id = props.getPropertyWithDefault("ServerID", "UnknownServer")
    logger.info(f"Iniciando servidor: {server_id}")

    media_path = props.getPropertyWithDefault('MediaServer.Content', 'media')
    playlists_path = props.getPropertyWithDefault('MediaServer.Playlists', 'playlists')
    users_path = props.getPropertyWithDefault("MediaServer.UsersFile", "users.json")
    
    servant = MediaServerI(Path(media_path), Path(playlists_path), Path(users_path))
    
    adapter = ic.createObjectAdapter("MediaServerAdapter")
    # Identidad fija para replica group
    adapter.add(servant, ic.stringToIdentity("MediaServer"))
    
    adapter.activate()
    ic.waitForShutdown()

if __name__ == "__main__":
    with Ice.initialize(sys.argv) as communicator:
        main(communicator)