#!/usr/bin/env python3

import logging
import sys
from contextlib import contextmanager
import Ice
from Ice import identityToString as id2str

# Intentamos importar el player real, si falla usamos uno simulado (Mock)
try:
    from gst_player import GstPlayer
    USING_MOCK = False
except ImportError:
    logger.warning("gst_player.py no encontrado. Usando MockPlayer simulado.")
    USING_MOCK = True
    class GstPlayer:
        def __init__(self): self.playing = False
        def is_playing(self): return self.playing
        def stop(self): self.playing = False; return True
        def configure(self, cb): pass
        def confirm_play_starts(self): self.playing = True; return True
        def start(self): pass
        def shutdown(self): pass

# Cargar Slice
Ice.loadSlice('-I{} spotifice_v2.ice'.format(Ice.getSliceDir()))
import Spotifice

logging.basicConfig(level=logging.INFO, format='[%(levelname)s] %(name)s: %(message)s')
logger = logging.getLogger("MediaRender")

class MediaRenderI(Spotifice.MediaRender):
    """Implementación del cliente reproductor."""
    def __init__(self, player_backend):
        self.player = player_backend
        self.server = None  # Proxy al MediaServer
        self.secure = None  # Proxy al SecureStreamManager (sesión)
        self.current_track = None
        self.playlist = None
        self.index = -1
        self.repeat = False
        self._paused = False

    def ensure_server_bound(self):
        if not self.server: raise Spotifice.BadReference(reason="No hay MediaServer vinculado")

    # --- Conectividad ---
    def bind_media_server(self, media_server, secure_stream_mgr, current=None):
        if not media_server or not secure_stream_mgr:
             raise Spotifice.BadReference("Proxies de servidor o sesión inválidos")
        self.server = media_server
        self.secure = secure_stream_mgr
        logger.info(f"Vinculado a MediaServer: {id2str(media_server.ice_getIdentity())}")

    def unbind_media_server(self, current=None):
        self.server = None
        self.secure = None
        logger.info("Desvinculado del MediaServer")

    # --- Gestión de Contenido ---
    def load_track(self, track_id, current=None):
        self.ensure_server_bound()
        logger.info(f"Solicitando pista: {track_id}")
        self.current_track = self.server.get_track_info(track_id)
        logger.info(f"Pista cargada: '{self.current_track.title}'")

    def load_playlist(self, playlist_id, current=None):
        self.ensure_server_bound()
        logger.info(f"Solicitando playlist: {playlist_id}")
        pl = self.server.get_playlist(playlist_id)
        self.playlist = pl
        self.index = 0
        if pl.track_ids:
            # Cargar la primera pista automáticamente
            self.current_track = self.server.get_track_info(pl.track_ids[0])
        logger.info(f"Playlist '{pl.name}' cargada con {len(pl.track_ids)} pistas.")

    # --- Control de Reproducción ---
    @contextmanager
    def keep_playing_state(self, current):
        """Helper para mantener el estado de reproducción tras cambiar de pista."""
        was_playing = self.player.is_playing()
        if was_playing: self.stop(current)
        try: yield
        finally: 
            if was_playing and self.current_track: self.play(current)

    def play(self, current=None):
        if not self.secure: raise Spotifice.BadReference("No hay sesión segura establecida (Login primero)")
        if not self.current_track: raise Spotifice.TrackError("No hay pista cargada para reproducir")
        
        logger.info(f"Iniciando reproducción de: {self.current_track.title}")
        # 1. Abrir stream en el servidor
        self.secure.open_stream(self.current_track.id)
        
        # 2. Definir callback para que el player pida datos
        def get_chunk_adapter(size):
            try: return self.secure.get_audio_chunk(size)
            except Exception as e:
                 logger.error(f"Error en stream: {e}")
                 return b"" # EOF o error
        
        # 3. Configurar y arrancar player
        self.player.configure(get_chunk_adapter)
        if not self.player.confirm_play_starts(): raise Spotifice.PlayerError("El backend de audio falló al iniciar")
        self._paused = False

    def stop(self, current=None):
        if self.player.is_playing() or self._paused:
            self.player.stop()
        if self.secure: 
            try: self.secure.close_stream()
            except: pass # Ignorar errores al cerrar
        self._paused = False
        logger.info("Reproducción detenida.")

    def pause(self, current=None):
        if self.player.is_playing():
             self.player.stop()
             self._paused = True
             logger.info("Reproducción pausada.")

    def get_status(self, current=None):
        state = Spotifice.PlaybackState.STOPPED
        if self.player.is_playing(): state = Spotifice.PlaybackState.PLAYING
        elif self._paused: state = Spotifice.PlaybackState.PAUSED
        
        tid = self.current_track.id if self.current_track else ""
        return Spotifice.PlaybackStatus(state=state, current_track_id=tid, repeat=self.repeat)

    def next(self, current=None):
        if not self.playlist or not self.playlist.track_ids: return
        with self.keep_playing_state(current):
            self.index = (self.index + 1) % len(self.playlist.track_ids)
            tid = self.playlist.track_ids[self.index]
            self.current_track = self.server.get_track_info(tid)
            logger.info(f"Saltando a siguiente pista: {self.current_track.title}")

    def previous(self, current=None):
        if not self.playlist or not self.playlist.track_ids: return
        with self.keep_playing_state(current):
            # Lógica simple de anterior (sin tener en cuenta segundos reproducidos)
            self.index = (self.index - 1 + len(self.playlist.track_ids)) % len(self.playlist.track_ids)
            tid = self.playlist.track_ids[self.index]
            self.current_track = self.server.get_track_info(tid)
            logger.info(f"Volviendo a pista anterior: {self.current_track.title}")

    def set_repeat(self, value, current=None):
         self.repeat = value
         logger.info(f"Repetición {'activada' if value else 'desactivada'}.")
         
    def get_current_track(self, current=None): return self.current_track

# --- Main ---

def main(ic, player_backend):
    # --- LECTURA DINÁMICA DE IDENTIDAD ---
    # Esta es la clave para que funcione en el Nivel Intermedio.
    # IceGrid pasa la propiedad "Identity" definida en el XML.
    properties = ic.getProperties()
    identity_str = properties.getPropertyWithDefault("Identity", "RenderGenerico")
    
    adapter = ic.createObjectAdapter("MediaRenderAdapter")
    servant = MediaRenderI(player_backend)
    
    # Registramos el sirviente con el nombre específico que nos dio IceGrid
    proxy = adapter.add(servant, ic.stringToIdentity(identity_str))
    
    logger.info(f"MediaRender iniciado con identidad: '{identity_str}'")
    logger.info(f"Proxy del adaptador: {proxy}")
    
    adapter.activate()
    ic.waitForShutdown()

if __name__ == "__main__":
    # Inicializar backend de audio (Real o Mock)
    player_backend = GstPlayer()
    player_backend.start()
    
    try:
        with Ice.initialize(sys.argv) as communicator:
            main(communicator, player_backend)
    except KeyboardInterrupt:
        logger.info("Interrumpido por el usuario.")
    finally:
        player_backend.shutdown()
