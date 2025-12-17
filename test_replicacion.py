#!/usr/bin/env python3
import sys, Ice, time
Ice.loadSlice('-I{} spotifice_v2.ice'.format(Ice.getSliceDir()))
import Spotifice

def main():
    with Ice.initialize(["--Ice.Config=locator.config"]) as communicator:
        print("--- TEST DE REPLICACIÓN Y BALANCEO DE CARGA (Round Robin) ---")
        
        # Realizamos 4 intentos para verificar la alternancia de nodos
        for i in range(1, 5):
            print(f"\n[INFO] Intento {i}: Solicitando conexión a 'MediaServer'...")
            
            # Conexión al GRUPO de réplicas
            base = communicator.stringToProxy("MediaServer")
            
            try:
                server = Spotifice.MediaServerPrx.checkedCast(base)
                if not server:
                    print("[ERROR] Proxy Inválido")
                    continue
                
                print(f"[OK] Conexión establecida con miembro del grupo de réplicas.")
                
                try:
                    # Intento de autenticación dummy para generar tráfico
                    server.authenticate(None, "user", "secret")
                except:
                    pass 
                
            except Exception as e:
                print(f"[ERROR] Excepción: {e}")
            
            time.sleep(1)

if __name__ == "__main__":
    main()