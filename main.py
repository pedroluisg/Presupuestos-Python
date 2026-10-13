import os
import sqlite3
from dotenv import load_dotenv
from google import genai
from pydantic import BaseModel
from typing import List, Optional
from fastapi import FastAPI


# ==========================================
# 0. CONFIGURACIÓN INICIAL DE LA API DE GEMINI
# ==========================================
load_dotenv()

api_key = os.getenv("GEMINI_API_KEY")

if not api_key:
    raise ValueError("❌ Error: No se ha encontrado la variable GEMINI_API_KEY en tu archivo .env")

client = genai.Client(api_key=api_key)

app = FastAPI()

@app.get("/")
def leer_raiz():
    return {"estado": "¡El Agente Electricista está activo y listo en Render!"}

# (Aquí mantienes tus funciones de procesar nota de voz, SQLite, etc.)


# ==========================================
# 1. DEFINICIÓN DE ESTRUCTURAS (Pydantic)
# ==========================================
class LineaDesglose(BaseModel):
    servicio_id: str
    concepto: str
    cantidad: int
    precio_unitario_mano_obra: float
    subtotal_mano_obra: float

class MaterialItem(BaseModel):
    material: str
    cantidad: int
    es_material_furgoneta: Optional[bool] = True # Indica si es habitual o externo

class PresupuestoEstructurado(BaseModel):
    cliente: str
    urgencia: str
    resumen_actuacion: str
    desglose_lineas: List[LineaDesglose]
    materiales_a_utilizar: List[MaterialItem]
    total_estimado_mano_obra: float
    tiempo_total_estimado_horas: float
    observaciones_imprevistos: Optional[str] = None


# ==========================================
# 2. CAPA 2: PROCESAMIENTO DE VOZ CON GEMINI
# ==========================================
def procesar_nota_de_voz(ruta_audio: str, catalogo_precios: dict):
    print("🎙️ Subiendo nota de voz a Gemini...")
    audio_file = client.files.upload(file=ruta_audio)

    # Prompt mejorado para lenguaje coloquial y manejo seguro de dudas
    prompt_sistema = f"""
    Eres el asistente operativo experto de un electricista autónomo. El profesional habla con lenguaje coloquial, directo y de la calle (por ejemplo, usando expresiones como "el chisme del cuadro", "un par de enchufes", "tirar cable por la rozas", etc.).

    Tus directrices:
    1. INTERPRETACIÓN: Extrae la información de la nota de voz traduciendo esa jerga coloquial a conceptos técnicos y limpios para el presupuesto.
    2. CRUCE DE DATOS: Compara los servicios detectados con este catálogo oficial de precios:
    {catalogo_precios}
    3. RED DE SEGURIDAD (MUY IMPORTANTE): Si hay algún dato ambiguo, un material que no esté claro, un precio que no encaje con total certeza o algo que la nota de voz no especifique bien, **NO inventes datos**. Pon una advertencia clara y detallada en el campo de 'observaciones_imprevistos' para que el electricista pueda revisarlo y ajustarlo antes de enviárselo al cliente.
    """

    print("🤖 Analizando y estructurando datos con Gemini (modo robusto)...")
    response = client.models.generate_content(
        model='models/gemini-3.8-flash',
        contents=[audio_file, prompt_sistema],
        config={
            'response_mime_type': 'application/json',
            'response_schema': PresupuestoEstructurado,
        },
    )
    return response.parsed


# ==========================================
# 3. CAPA 3: INVENTARIO Y CESTA DE COMPRA (SQLite)
# ==========================================
def inicializar_base_de_datos():
    conexion = sqlite3.connect("electricista.db")
    cursor = conexion.cursor()
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS inventario_furgoneta (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre_material TEXT UNIQUE,
            stock_actual INTEGER,
            stock_minimo INTEGER
        )
    """)
    
    cursor.execute("SELECT COUNT(*) FROM inventario_furgoneta")
    if cursor.fetchone() == 0:
        materiales_iniciales = [
            ("Enchufes", 10, 5),
            ("Cable eléctrico (metros)", 50, 20),
            ("Interruptor diferencial", 3, 2)
        ]
        cursor.executemany("""
            INSERT INTO inventario_furgoneta (nombre_material, stock_actual, stock_minimo)
            VALUES (?, ?, ?)
        """, materiales_iniciales)
        conexion.commit()
    
    conexion.close()

def actualizar_inventario_y_generar_cesta(materiales_utilizados):
    conexion = sqlite3.connect("electricista.db")
    cursor = conexion.cursor()
    cesta_compra = []
    
    print("\n----------------------------------------")
    print("🔄 Analizando stock e infiriendo compras...")
    print("----------------------------------------")
    
    for item in materiales_utilizados:
        material_nombre = item.material
        cantidad_usada = item.cantidad
        
        # Buscamos si existe con coincidencia parcial (LIKE) en la furgoneta
        cursor.execute("""
            SELECT nombre_material, stock_actual, stock_minimo 
            FROM inventario_furgoneta 
            WHERE nombre_material LIKE ?
        """, (f"%{material_nombre}%",))
        
        resultado = cursor.fetchone()
        
        if resultado:
            # Caso A: El material está en la furgoneta
            nombre_db, stock_actual, stock_minimo = resultado
            nuevo_stock = stock_actual - cantidad_usada
            
            cursor.execute("""
                UPDATE inventario_furgoneta 
                SET stock_actual = ? 
                WHERE nombre_material = ?
            """, (nuevo_stock, nombre_db))
            
            print(f"✔️ [Stock furgoneta] {nombre_db}: Usados {cantidad_usada}. Quedan {nuevo_stock}.")
            
            if nuevo_stock < stock_minimo:
                unidades_a_pedir = stock_minimo - nuevo_stock
                cesta_compra.append({
                    "material": nombre_db,
                    "cantidad_a_comprar": unidades_a_pedir,
                    "tipo": "Reposición de stock"
                })
        else:
            # Caso B: Material nuevo o específico (no está en la furgoneta)
            print(f"📦 [Material especial / Nuevo] '{material_nombre}' (Cantidad: {cantidad_usada}): No registrado en furgoneta. Añadido directo a cesta.")
            cesta_compra.append({
                "material": material_nombre,
                "cantidad_a_comprar": cantidad_usada,
                "tipo": "Encargo específico para obra"
            })
            
    conexion.commit()
    conexion.close()
    return cesta_compra


# ==========================================
# 4. EJECUCIÓN PRINCIPAL DEL CICLO COMPLETO
# ==========================================
if __name__ == "__main__":
    inicializar_base_de_datos()

    catalogo_ejemplo = {
        "servicios": [
            {"id": "SERV_01", "nombre": "Instalación de punto de luz o enchufe", "precio": 35.00},
            {"id": "SERV_02", "nombre": "Cambio o sustitución de cuadro eléctrico / diferencial", "precio": 120.00},
            {"id": "SERV_03", "nombre": "Tendido de cable adicional (por metro)", "precio": 3.00}
        ]
    }

    ruta_audio_prueba = "visita_juan.mp3" 

    if os.path.exists(ruta_audio_prueba):
        try:
            resultado = procesar_nota_de_voz(ruta_audio_prueba, catalogo_ejemplo)
            
            print("\n========================================")
            print("📋 PRESUPUESTO GENERADO PARA EL CLIENTE")
            print("========================================")
            print(f"👤 Cliente: {resultado.cliente}")
            print(f"⚡ Urgencia: {resultado.urgencia}")
            print(f"📝 Resumen: {resultado.resumen_actuacion}")
            print(f"🛠️  Desglose de Mano de Obra:")
            for linea in resultado.desglose_lineas:
                print(f"   - {linea.cantidad}x {linea.concepto} -> {linea.subtotal_mano_obra} €")
            print(f"📦 Materiales a utilizar:")
            for mat in resultado.materiales_a_utilizar:
                print(f"   - {mat.cantidad}x {mat.material}")
            print(f"💶 TOTAL MANO DE OBRA: {resultado.total_estimado_mano_obra} €")
            print("========================================")

            cesta_generada = actualizar_inventario_y_generar_cesta(resultado.materiales_a_utilizar)

            print("\n----------------------------------------")
            print("🛒 CESTA DE COMPRA Y ENCARGOS A PROVEEDOR")
            print("----------------------------------------")
            if cesta_generada:
                for item in cesta_generada:
                    print(f" 👉 [{item['tipo']}] Pedir: {item['cantidad_a_comprar']}x '{item['material']}'")
            else:
                print(" ✨ Stock óptimo y sin materiales externos requeridos.")
            print("----------------------------------------")
            
        except Exception as e:
            print(f"❌ Error durante la ejecución del agente: {e}")
    else:
        print(f"⚠️ No se encuentra el archivo '{ruta_audio_prueba}' en la carpeta de tu workspace.")