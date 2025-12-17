#!/usr/bin/env python3
import sys
import Ice

# Cargar la interfaz
try:
    Ice.loadSlice('-I{} spotifice_v2.ice'.format(Ice.getSliceDir()))
    import Spotifice
except ImportError:
    print("[FATAL] No se pudo cargar el fichero Slice 'spotifice_v2.ice'.")
    sys.exit(1)

def test_replica_group(communicator):
    print("\n[TEST 1] Probando conexión al Grupo de Réplicas 'MediaServer'...")
    try:
        # Conectamos a la identidad abstracta del grupo
        base = communicator.stringToProxy("MediaServer")
        server = Spotifice.MediaServerPrx.checkedCast(base)
        if not server:
             print("❌ [ERROR] Proxy inválido para 'MediaServer'.")
             return False
        
        # Probamos una operación simple (listar tracks)
        tracks = server.get_all_tracks()
        print(f"✅ [OK] Conexión exitosa al grupo. Pistas disponibles: {len(tracks)}")
        if tracks:
             print(f"   Ejemplo de pista del servidor: {tracks[0].title}")
        return True
    except Ice.Exception as e:
        print(f"❌ [ERROR] Falló la conexión al grupo de réplicas: {e}")
        return False

def test_specific_renders(communicator):
    print("\n[TEST 2] Probando conexión a Renders específicos (Requisito: 2 Renders)...")
    # Nombres definidos en spotifice.xml
    renders_to_test = ["mediaRender1", "mediaRender2"] 
    success_count = 0

    for render_name in renders_to_test:
        print(f" -> Intentando conectar con '{render_name}'...")
        try:
            base = communicator.stringToProxy(render_name)
            # Usamos checkedCast para verificar que el objeto existe y es del tipo correcto
            render = Spotifice.MediaRenderPrx.checkedCast(base)
            
            if render:
                print(f"   ✅ [OK] '{render_name}' localizado y responde.")
                success_count += 1
            else:
                print(f"   ❌ [ERROR] IceGrid no pudo resolver '{render_name}'.")
                
        except Ice.Exception as e:
             print(f"   ❌ [ERROR] Excepción al contactar '{render_name}': {e}")

    print(f"\nResultado Renders: {success_count}/{len(renders_to_test)} operativos.")
    return success_count == len(renders_to_test)

def main():
    # Usamos locator.config para que el cliente sepa dónde está el Registry
    try:
        with Ice.initialize(["--Ice.Config=locator.config"]) as communicator:
            print("--- INICIO TEST DE NIVEL INTERMEDIO (IceGrid) ---")
            
            group_ok = test_replica_group(communicator)
            renders_ok = test_specific_renders(communicator)

            print("\n--- RESUMEN FINAL ---")
            if group_ok and renders_ok:
                print("✅✅✅  NIVEL INTERMEDIO COMPLETADO CORRECTAMENTE  ✅✅✅")
                print("La infraestructura cumple con todos los requisitos detectados.")
            else:
                print("⚠️  [ATENCIÓN] Alguna prueba ha fallado. Revisa los logs anteriores.")

    except Exception as e:
        print(f"[FATAL] Error inesperado en el cliente de prueba: {e}")

if __name__ == "__main__":
    main()