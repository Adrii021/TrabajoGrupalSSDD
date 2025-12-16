#!/usr/bin/env python3
import sys
import Ice

# Cargamos la interfaz v2 (la del Hito 2)
Ice.loadSlice('-I{} spotifice_v2.ice'.format(Ice.getSliceDir()))
import Spotifice

def main():
    # CAMBIO 1: Iniciamos Ice cargando el fichero locator.config
    # Esto configura automáticamente el "Locator"
    with Ice.initialize(["--Ice.Config=locator.config"]) as communicator:
        
        print("🔍 Consultando al Registry de IceGrid...")

        # CAMBIO 2: Ya no ponemos puertos (:default -p 10000)
        # Solo ponemos el NOMBRE del objeto (Identity) que definimos en spotifice.xml
        base_server = communicator.stringToProxy("mediaServer1")
        base_render = communicator.stringToProxy("mediaRender1")

        # El resto es igual: Casteamos los proxies
        server = Spotifice.MediaServerPrx.checkedCast(base_server)
        render = Spotifice.MediaRenderPrx.checkedCast(base_render)

        if not server or not render:
            print("❌ Error: IceGrid no encontró los objetos. ¿Está corriendo deploy.sh?")
            return

        print("✅ ¡Objetos localizados a través de IceGrid!")

        try:
            # Prueba rápida de la lógica del Hito 2
            print("🔐 Intentando autenticar...")
            session = server.authenticate(render, "user", "secret")
            print(f"✅ Autenticación correcta. Sesión: {session}")
            
            # Limpieza
            session.close()
            print("👋 Sesión cerrada.")
            
        except Exception as e:
            print(f"❌ Error durante la prueba: {e}")

if __name__ == "__main__":
    main()