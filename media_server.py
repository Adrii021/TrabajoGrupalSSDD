#!/usr/bin/env python3

import logging
import sys
from pathlib import Path
import Ice
import json
from datetime import datetime, timezone
import hashlib, secrets

# Cargar la definición de la interfaz Slice
Ice.loadSlice('-I{} spotifice_v2.ice'.format(Ice.getSliceDir()))
import Spotifice

logging.basicConfig(level=logging.INFO, format='[%(levelname)s] %(name)s: %(message)s')
logger = logging.getLogger("MediaServer")

# --- Funciones Auxiliares de Autenticación y Fecha ---

def _verify_password(password: str, salt: str, digest: str) -> bool:
    """Verifica si una contraseña en texto plano coincide con el hash almacenado."""
    calc = hashlib.md5((password + salt).encode('utf-8')).hexdigest()
    return secrets.compare_digest(calc, digest)

def _parse_created_at(v):
    """Intenta convertir varios formatos de fecha a timestamp Unix."""
    if isinstance(v, (int, float)): return int(v)
    if isinstance(v, str) and v.strip():
        try:
            dt = datetime.strptime(v.strip(), "%d-%m-%Y")
            return int(dt.replace(tzinfo=timezone.utc).timestamp())
        except: pass
    return int(datetime.now(timezone.utc).timestamp()) # Default: ahora

# --- Implementación de Sirvientes (Servants) ---

class SecureStreamManagerI(Spotifice.SecureStreamManager):
    """Maneja la transmisión segura de ficheros para un usuario autenticado."""
    def __init__(self, server_impl, username):
        self._server = server_impl
        self._username = username
        self._fh = None # File handle actual

    def open_stream(self, track_id, current=None):
        """Abre un fichero de música para lectura."""
        self.close_stream(current) # Cierra anterior si existe
        info = self._server.get_track_info(track_id)
        path = self._server.media_dir / info.filename
        try:
            logger.info(f"Abriendo stream para: {info.filename}")
            self._fh = open(path, "rb")
        except FileNotFoundError:
            raise Spotifice.IOError(item=track_id, reason="Fichero no encontrado en disco")
        except Exception as e:
            raise Spotifice.IOError(item=track_id, reason=f"Error de E/S: {e}")

    def get_audio_chunk(self, chunk_size, current=None):
        """Lee un trozo del fichero abierto."""
        if not self._fh: raise Spotifice.StreamError("No hay stream abierto")
        try:
            data = self._fh.read(chunk_size)
            return bytearray(data or b"") # Devuelve array vacío al final
        except Exception as e:
             raise Spotifice.StreamError(f"Error leyendo chunk: {e}")

    def close_stream(self, current=None):
        """Cierra el handle del fichero actual."""
        if self._fh:
            self._fh.close()
            self._fh = None
            logger.debug("Stream cerrado.")

    def close(self, current=None):
        """Cierra la sesión completa y elimina el sirviente."""
        self.close_stream(current)
        self._server.remove_session(self._username)
        logger.info(f"Sesión finalizada para usuario: {self._username}")

class MediaServerI(Spotifice.MediaServer):
    """Implementación principal del servidor de medios."""
    def __init__(self, media_dir, playlists_dir, users_file: Path):
        self.media_dir = Path(media_dir)
        self.playlists_dir = Path(playlists_dir)
        self.tracks = {}      # {track_id: TrackInfo}
        self._playlists = {}  # {playlist_id: Playlist}
        self._sessions = {}   # {username: SecureStreamManagerI}
        self._users = {}      # {username: {salt, digest}}
        
        self.load_media()
        self.load_playlists()
        self.load_users(users_file)

    def load_users(self, users_file):
        if users_file.exists():
            try:
                raw = json.loads(users_file.read_text(encoding='utf-8'))
                # Limpieza de espacios en blanco en claves y valores
                for k, v in raw.items():
                    self._users[k.strip()] = {sk.strip(): sv.strip() if isinstance(sv, str) else sv for sk, sv in v.items()}
                logger.info(f"Cargados {len(self._users)} usuarios desde {users_file.name}")
            except Exception as e:
                logger.error(f"Error cargando {users_file.name}: {e}")
        else:
             logger.warning(f"Fichero de usuarios {users_file.name} no encontrado.")

    def load_playlists(self):
        self._playlists.clear()
        self.playlists_dir.mkdir(parents=True, exist_ok=True)
        for f in sorted(self.playlists_dir.glob("*.playlist")):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                # Filtrar IDs de pistas que no existen en la biblioteca
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
            except Exception as e:
                logger.warning(f"Saltando playlist corrupta '{f.name}': {e}")
        logger.info(f"Cargadas {len(self._playlists)} playlists disponibles.")

    def load_media(self):
        self.tracks.clear()
        if not self.media_dir.exists():
             logger.warning(f"Directorio de medios {self.media_dir} no existe.")
             return
        for f in sorted(self.media_dir.iterdir()):
            if f.is_file() and f.suffix.lower() == ".mp3":
                # Usamos el nombre del fichero como ID y título base
                self.tracks[f.name] = Spotifice.TrackInfo(id=f.name, title=f.stem, filename=f.name)
        logger.info(f"Indexadas {len(self.tracks)} pistas de audio en {self.media_dir}.")

    # --- Implementación de interfaces Slice ---

    def get_all_tracks(self, current=None): return list(self.tracks.values())
    
    def get_track_info(self, track_id, current=None): 
        if track_id not in self.tracks: raise Spotifice.TrackError(track_id, "Pista no encontrada")
        return self.tracks[track_id]
        
    def get_all_playlists(self, current=None): return list(self._playlists.values())
    
    def get_playlist(self, playlist_id, current=None):
        if playlist_id not in self._playlists: raise Spotifice.PlaylistError(playlist_id, "Playlist no encontrada")
        return self._playlists[playlist_id]

    def authenticate(self, media_render, username, password, current=None):
        if not media_render: raise Spotifice.BadReference("Render cliente inválido (None)")
        
        # Verificación de credenciales
        if username not in self._users:
             logger.warning(f"Usuario desconocido: {username}")
             raise Spotifice.AuthError("Credenciales inválidas", username)
        
        u = self._users[username]
        if not _verify_password(password, u["salt"], u["digest"]):
             logger.warning(f"Contraseña incorrecta para: {username}")
             raise Spotifice.AuthError("Credenciales inválidas", username)
        
        # Crear sesión segura
        servant = SecureStreamManagerI(self, username)
        # Registrar con UUID para que sea único por sesión
        proxy = current.adapter.addWithUUID(servant)
        self._sessions[username] = servant
        logger.info(f"Autenticación exitosa para usuario: {username}. Sesión creada.")
        return Spotifice.SecureStreamManagerPrx.uncheckedCast(proxy)

    def remove_session(self, username):
         self._sessions.pop(username, None)

# --- Función Principal ---

def main(ic):
    props = ic.getProperties()
    server_id = props.getPropertyWithDefault("ServerID", "UnknownServer")
    logger.info(f"Iniciando servidor. ID de instancia: {server_id}")

    # Cargar rutas desde configuración
    media_path = props.getPropertyWithDefault('MediaServer.Content', 'media')
    playlists_path = props.getPropertyWithDefault('MediaServer.Playlists', 'playlists')
    users_path = props.getPropertyWithDefault("MediaServer.UsersFile", "users.json")
    
    servant = MediaServerI(Path(media_path), Path(playlists_path), Path(users_path))
    
    adapter = ic.createObjectAdapter("MediaServerAdapter")
    
    # IMPORTANTE PARA REPLICA GROUP:
    # Registramos el sirviente con la identidad fija "MediaServer".
    # IceGrid usará esto para el balanceo de carga entre nodos.
    adapter.add(servant, ic.stringToIdentity("MediaServer"))
    
    adapter.activate()
    logger.info("MediaServerAdapter activo y esperando peticiones.")
    ic.waitForShutdown()
    logger.info("Apagando servidor.")

if __name__ == "__main__":
    with Ice.initialize(sys.argv) as communicator:
        main(communicator)
