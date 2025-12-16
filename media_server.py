#!/usr/bin/env python3

import logging
import sys
from pathlib import Path
import uuid

import Ice
from Ice import identityToString as id2str

import json
from datetime import datetime, timezone

import hashlib, secrets, json
from pathlib import Path


Ice.loadSlice('-I{} spotifice_v2.ice'.format(Ice.getSliceDir()))
import Spotifice  # type: ignore # noqa: E402

def _parse_created_at(v):
            # Acepta int/float ya válidos
            if isinstance(v, (int, float)):
                return int(v)
            # Acepta string "dd-mm-YYYY" del enunciado
            if isinstance(v, str) and v.strip():
                try:
                    dt = datetime.strptime(v.strip(), "%d-%m-%Y")
                    return int(dt.replace(tzinfo=timezone.utc).timestamp())
                except Exception:
                    pass
            # Fallback seguro
            return 0

def _verify_password(password: str, salt: str, digest: str) -> bool:
        calc = hashlib.md5((password + salt).encode('utf-8')).hexdigest()
        return secrets.compare_digest(calc, digest)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("MediaServer")


class StreamedFile:
    def __init__(self, track_info, media_dir):
        self.track = track_info
        filepath = media_dir / track_info.filename

        try:
            self.file = open(filepath, 'rb')
        except Exception as e:
            raise Spotifice.IOError(track_info.filename, f"Error opening media file: {e}")
        
    
    def read(self, size):
        return self.file.read(size)

    def close(self):
        try:
            if self.file:
                self.file.close()
        except Exception as e:
            logger.error(f"Error closing file for track '{self.track.id}': {e}")

    def __repr__(self):
        return f"<StreamState '{self.track.id}'>"


class MediaServerI(Spotifice.MediaServer):
    def __init__(self, media_dir, playlists_dir, users_file: Path):
        self.media_dir = Path(media_dir)
        self.playlists_dir = Path(playlists_dir)
        self.tracks = {}
        self._playlists = {}
        self.active_streams = {}
        self._sessions = {}


        self.load_media()
        self.load_playlists()

        self._users = {}  # username -> dict
        if users_file.exists():
            raw = json.loads(users_file.read_text(encoding='utf-8'))
            self._users = {}

            for key, info in raw.items():
                clean_key = key.strip()                  # " user " → "user"
                clean_info = {}

                for k, v in info.items():
                    clean_k = k.strip()                  # " digest " → "digest"
                    clean_v = v.strip() if isinstance(v, str) else v
                    clean_info[clean_k] = clean_v

                self._users[clean_key] = clean_info

    def _verify(self, password, salt, digest):
        import hashlib
        salted = (salt + password).encode("utf-8")
        return hashlib.md5(salted).hexdigest() == digest



    def load_playlists(self):
        """Carga todas las playlists de playlists/*.playlist al iniciar."""
        self._playlists.clear()
        self.playlists_dir.mkdir(parents=True, exist_ok=True)

        for f in sorted(self.playlists_dir.glob("*.playlist")):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                valid_ids = [tid for tid in data.get("track_ids", []) if tid in self.tracks]
                pl = Spotifice.Playlist(
                    id=data["id"],
                    name=data.get("name", data["id"]),
                    description=data.get("description", ""),
                    owner=data.get("owner", ""),
                    created_at=_parse_created_at(data.get("created_at")),
                    track_ids=valid_ids,
                )

                self._playlists[pl.id] = pl
            except Exception as e:
                logger.error(f"Invalid playlist '{f.name}': {e}")

        logger.info(f"Load playlists: {len(self._playlists)} playlists")


    def ensure_track_exists(self, track_id):
        if track_id not in self.tracks:
            raise Spotifice.TrackError(track_id, "Track not found")

    def load_media(self):
        for filepath in sorted(Path(self.media_dir).iterdir()):
            if not filepath.is_file() or filepath.suffix.lower() != ".mp3":
                continue

            self.tracks[filepath.name] = self.track_info(filepath)

        logger.info(f"Load media:  {len(self.tracks)} tracks")

    @staticmethod
    def track_info(filepath):
        return  Spotifice.TrackInfo(
            id=filepath.name,
            title=filepath.stem,
            filename=filepath.name)

    # ---- MusicLibrary ----
    def get_all_tracks(self, current=None):
        return list(self.tracks.values())

    def get_track_info(self, track_id, current=None):
        self.ensure_track_exists(track_id)
        return self.tracks[track_id]

    # ---- StreamManager ----
    def open_stream(self, track_id, render_id, current=None):
        str_render_id = id2str(render_id)
        self.ensure_track_exists(track_id)

        if not render_id.name:
            raise Spotifice.BadIdentity(str_render_id, "Invalid render identity")

        self.active_streams[str_render_id] = StreamedFile(
            self.tracks[track_id], self.media_dir)

        logger.info("Open stream for track '{}' on render '{}'".format(
            track_id, str_render_id))

    def close_stream(self, render_id, current=None):
        str_render_id = id2str(render_id)
        if stream_state := self.active_streams.pop(str_render_id, None):
            stream_state.close()
            logger.info(f"Closed stream for render '{str_render_id}'")

    def get_audio_chunk(self, render_id, chunk_size, current=None):
        str_render_id = id2str(render_id)
        try:
            streamed_file = self.active_streams[str_render_id]
        except KeyError:
            raise Spotifice.StreamError(str_render_id, "No open stream for render")

        try:
            data = streamed_file.read(chunk_size)
            if not data:
                logger.info(f"Track exhausted: '{streamed_file.track.id}'")
                self.close_stream(render_id, current)
            return data

        except Exception as e:
            raise Spotifice.IOError(
                streamed_file.track.filename, f"Error reading file: {e}")

        # ---- PlaylistManager ----
    def get_all_playlists(self, current=None):
        return list(self._playlists.values())

    def get_playlist(self, playlist_id, current=None):
        pl = self._playlists.get(playlist_id)
        if pl:
            return pl
        raise Spotifice.PlaylistError(item=playlist_id, reason="Playlist not found")
    
    def authenticate(self, media_render, username, password, current=None):

        # 1) Validar referencia REMOTA correctamente
        if media_render is None: # Ice se encarga del tipo, basta chequear None
            raise Spotifice.BadReference(reason="invalid media render")

        # 2) Comprobar usuario
        if username not in self._users:
            raise Spotifice.AuthError(reason="invalid credentials", item=username)

        user = self._users[username]

        # 3) Verificar contraseña CORRECTAMENTE (Password + Salt)
        # Usamos la función auxiliar que ya tenías definida arriba pero no usabas
        if not _verify_password(password, user["salt"], user["digest"]):
            raise Spotifice.AuthError(reason="invalid credentials", item=username)

        # 4) Crear el objeto de sesión (Sirviente)
        # Nota: current.id no es la identidad del render, es la identidad del server.
        # Pero para el hito 2 no es crítico.
        secure_servant = SecureStreamManagerI(self, media_render, username)

        # 5) Registrar el sirviente en el adaptador para obtener un PROXY
        # Esto genera una identidad única (UUID) para esta sesión
        proxy = current.adapter.addWithUUID(secure_servant)

        # 6) Guardar referencia (opcional, para gestión interna)
        self._sessions[username] = secure_servant

        logger.info(f"Sesión creada para usuario: {username}")

        # 7) Devolver el PROXY casteado correctamente
        return Spotifice.SecureStreamManagerPrx.uncheckedCast(proxy)



    

class SecureStreamManagerI(Spotifice.SecureStreamManager):
    def __init__(self, server, render_id, username):
        self._server = server
        self._render_id = render_id
        self._username = username
        self._fh = None

    def open_stream(self, track_id, current=None):
        self.close_stream(current)
        info = self._server.get_track_info(track_id)
        path = self._server.media_dir / info.filename

        try:
            self._fh = open(path, "rb")
        except FileNotFoundError:
            raise Spotifice.IOError(item=track_id, reason="file not found")

    def get_audio_chunk(self, chunk_size, current=None):
        if not self._fh:
            raise Spotifice.StreamError("no open stream")

        data = self._fh.read(chunk_size)
        return bytearray(data or b"")

    def close_stream(self, current=None):
        if self._fh:
            try:
                self._fh.close()
            finally:
                self._fh = None

    def close(self, current=None):
        self.close_stream(current)
        self._server._sessions.pop(self._username, None)




def main(ic):
    properties = ic.getProperties()
    media_dir = properties.getPropertyWithDefault('MediaServer.Content', 'media')
    playlists_dir = properties.getPropertyWithDefault('MediaServer.Playlists', 'playlists')
    users_file = properties.getPropertyWithDefault("MediaServer.UsersFile", "users.json")

    servant = MediaServerI(Path(media_dir), Path(playlists_dir), Path(users_file))

    adapter = ic.createObjectAdapter("MediaServerAdapter")
    proxy = adapter.add(servant, ic.stringToIdentity("mediaServer1"))
    logger.info(f"MediaServer: {proxy}")

    adapter.activate()
    ic.waitForShutdown()
    logger.info("Shutdown")



if __name__ == "__main__":
    # Eliminamos el check de len(sys.argv) < 2 porque IceGrid maneja los argumentos
    try:
        # CAMBIO CLAVE: Pasamos sys.argv entero, NO sys.argv[1]
        with Ice.initialize(sys.argv) as communicator:
            main(communicator)
    except KeyboardInterrupt:
        logger.info("Server interrupted by user.")