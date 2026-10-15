import os
import sqlite3
from dotenv import load_dotenv
from google import genai
from pydantic import BaseModel
from typing import List, Optional
from fastapi import FastAPI, UploadFile, File, Form
from fastapi.responses import HTMLResponse, FileResponse

# ReportLab para generar PDFs profesionales
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors

load_dotenv()
api_key = os.getenv("GEMINI_API_KEY")

if not api_key:
    raise ValueError("❌ Error: No se ha encontrado la variable GEMINI_API_KEY en tu archivo .env")

client = genai.Client(api_key=api_key)
app = FastAPI()

# ==========================================
# 1. ESTRUCTURAS Y BASE DE DATOS AMPLIADA
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
    es_material_furgoneta: Optional[bool] = True

class PresupuestoEstructurado(BaseModel):
    cliente: str
    urgencia: str
    resumen_actuacion: str
    desglose_lineas: List[LineaDesglose]
    materiales_a_utilizar: List[MaterialItem]
    total_estimado_mano_obra: float
    tiempo_total_estimado_horas: float
    observaciones_imprevistos: Optional[str] = None

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
        # Catálogo inicial de materiales ampliado (incluyendo secciones de cable y protecciones)
        materiales_iniciales = [
            ("Enchufes", 10, 5),
            ("Cable 1.5mm (metros)", 50, 20),
            ("Cable 2.5mm (metros)", 50, 20),
            ("Cable 6mm (metros)", 30, 10),
            ("Interruptor diferencial", 3, 2),
            ("Magnetotérmico", 4, 2)
        ]
        cursor.executemany("""
            INSERT INTO inventario_furgoneta (nombre_material, stock_actual, stock_minimo)
            VALUES (?, ?, ?)
        """, materiales_iniciales)
        conexion.commit()
    conexion.close()

def procesar_nota_de_voz_bytes(contenido_audio: bytes, nombre_archivo: str, catalogo_precios: dict):
    import json
    ruta_temp = f"temp_{nombre_archivo}"
    with open(ruta_temp, "wb") as f:
        f.write(contenido_audio)
        
    audio_file = client.files.upload(file=ruta_temp)

    prompt_sistema = f"""
    Eres el asistente operativo experto de un electricista autónomo. El profesional habla con lenguaje coloquial, directo y de la obra.
    
    DICCIONARIO DE TÉRMINOS Y JERGA ELÉCTRICA:
    - "El boletín" / "Certificado" -> Certificado de Instalación Eléctrica (CIE).
    - "Cable gordo" / "Cable de cocina" / "Cable para vitro" -> Cable de 6mm.
    - "Automático" / "Magneto" -> Magnetotérmico.
    - "Diferencial" / "El chisme que salta" -> Interruptor diferencial.
    - "Revisar la instalación" / "Revisión" -> Revisión y comprobación de seguridad eléctrica.
    - "Poner electrodoméstico" / "Conectar cocina" -> Instalación y configuración de electrodoméstico.

    Devuelve estrictamente un objeto JSON válido (sin formato markdown extra) con esta estructura exacta:
    {{
      "cliente": "Nombre del cliente detectado",
      "urgencia": "Normal o Urgente",
      "resumen_actuacion": "Resumen de lo realizado",
      "desglose_lineas": [
        {{"servicio_id": "SERV_01", "concepto": "Nombre del servicio", "cantidad": 1, "precio_unitario_mano_obra": 35.0, "subtotal_mano_obra": 35.0}}
      ],
      "materiales_a_utilizar": [
        {{"material": "Nombre del material", "cantidad": 2, "es_material_furgoneta": true}}
      ],
      "total_estimado_mano_obra": 35.0,
      "tiempo_total_estimado_horas": 1.0,
      "observaciones_imprevistos": null
    }}
    Compara los servicios con este catálogo oficial: {catalogo_precios}
    """

    response = client.models.generate_content(
        model='models/gemini-2.5-flash',
        contents=[audio_file, prompt_sistema],
        config={
            'response_mime_type': 'application/json',
        },
    )
    
    if os.path.exists(ruta_temp):
        os.remove(ruta_temp)
        
    # Parseamos el texto JSON devuelto por Gemini a un diccionario de Python puro
    return json.loads(response.text)

def actualizar_inventario_y_generar_cesta(materiales_utilizados):
    conexion = sqlite3.connect("electricista.db")
    cursor = conexion.cursor()
    cesta_compra = []
    
    for item in materiales_utilizados:
        # Cambiado a notación de diccionario []
        mat_nombre = item["material"]
        mat_cantidad = item["cantidad"]
        
        cursor.execute("SELECT nombre_material, stock_actual, stock_minimo FROM inventario_furgoneta WHERE nombre_material LIKE ?", (f"%{mat_nombre}%",))
        resultado = cursor.fetchone()
        
        if resultado:
            nombre_db, stock_actual, stock_minimo = resultado
            nuevo_stock = stock_actual - mat_cantidad
            cursor.execute("UPDATE inventario_furgoneta SET stock_actual = ? WHERE nombre_material = ?", (nuevo_stock, nombre_db))
            if nuevo_stock < stock_minimo:
                cesta_compra.append({"material": nombre_db, "cantidad_a_comprar": stock_minimo - nuevo_stock, "tipo": "Reposición stock"})
        else:
            cesta_compra.append({"material": mat_nombre, "cantidad_a_comprar": mat_cantidad, "tipo": "Encargo específico"})
            
    conexion.commit()
    conexion.close()
    return cesta_compra

def generar_pdf_presupuesto(presupuesto, nombre_comercial, telefono_autonomo, ruta_pdf="presupuesto.pdf"):
    doc = SimpleDocTemplate(ruta_pdf, pagesize=letter, rightMargin=40, leftMargin=40, topMargin=40, bottomMargin=40)
    story = []
    styles = getSampleStyleSheet()
    
    estilo_titulo = ParagraphStyle('TituloEmpresa', parent=styles['Heading1'], fontSize=20, textColor=colors.HexColor("#eab308"), spaceAfter=4)
    estilo_sub = ParagraphStyle('SubHeader', parent=styles['Normal'], fontSize=10, textColor=colors.HexColor("#64748b"), spaceAfter=15)
    estilo_h2 = ParagraphStyle('H2', parent=styles['Heading2'], fontSize=14, textColor=colors.HexColor("#1e293b"), spaceBefore=12, spaceAfter=6)
    estilo_texto = ParagraphStyle('Texto', parent=styles['Normal'], fontSize=10, textColor=colors.HexColor("#334155"), leading=14)
    
    story.append(Paragraph(f"⚡ {nombre_comercial}", estilo_titulo))
    story.append(Paragraph(f"Teléfono de contacto: {telefono_autonomo}", estilo_sub))
    
    # Cambiado todo a notación de diccionario []
    story.append(Paragraph("<b>Datos del Cliente y Servicio</b>", estilo_h2))
    story.append(Paragraph(f"<b>Cliente:</b> {presupuesto['cliente']}", estilo_texto))
    story.append(Paragraph(f"<b>Urgencia:</b> {presupuesto['urgencia']}", estilo_texto))
    story.append(Paragraph(f"<b>Actuación:</b> {presupuesto['resumen_actuacion']}", estilo_texto))
    story.append(Spacer(1, 10))
    
    story.append(Paragraph("<b>Desglose de Mano de Obra y Servicios</b>", estilo_h2))
    data_tabla = [["Concepto", "Cant.", "Precio U.", "Subtotal"]]
    for linea in presupuesto['desglose_lineas']:
        data_tabla.append([linea['concepto'], str(linea['cantidad']), f"{linea['precio_unitario_mano_obra']:.2f} €", f"{linea['subtotal_mano_obra']:.2f} €"])
    
    t = Table(data_tabla, colWidths=[240, 50, 80, 80])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor("#1e293b")),
        ('TEXTCOLOR', (0,0), (-1,0), colors.white),
        ('ALIGN', (1,0), (-1,-1), 'CENTER'),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('BOTTOMPADDING', (0,0), (-1,0), 6),
        ('BACKGROUND', (0,1), (-1,-1), colors.HexColor("#f8fafc")),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor("#cbd5e1")),
        ('FONTSIZE', (0,0), (-1,-1), 9),
    ]))
    story.append(t)
    story.append(Spacer(1, 10))
    
    story.append(Paragraph(f"<b>TOTAL ESTIMADO MANO DE OBRA: {presupuesto['total_estimado_mano_obra']:.2f} €</b>", ParagraphStyle('Total', parent=estilo_texto, fontSize=12, textColor=colors.HexColor("#166534"))))
    
    if presupuesto.get('observaciones_imprevistos'):
        story.append(Spacer(1, 10))
        story.append(Paragraph("<b>Observaciones / Notas:</b>", estilo_h2))
        story.append(Paragraph(presupuesto['observaciones_imprevistos'], estilo_texto))
        
    doc.build(story)
    return ruta_pdf


# ==========================================
# 2. RUTAS WEB Y VISTA MÓVIL
# ==========================================
@app.on_event("startup")
def startup_event():
    inicializar_base_de_datos()

@app.get("/", response_class=HTMLResponse)
def home():
    return """
    <!DOCTYPE html>
    <html lang="es">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Asistente Electricista Autónomo</title>
        <script src="https://cdn.tailwindcss.com"></script>
    </head>
    <body class="bg-slate-900 text-slate-100 min-h-screen p-4 flex flex-col items-center">
        <div class="w-full max-w-lg bg-slate-800 rounded-2xl shadow-xl p-6 border border-slate-700 mt-2">
            <h1 class="text-2xl font-bold text-yellow-400 mb-1 flex items-center gap-2">⚡ Presupuestos Flash</h1>
            <p class="text-slate-400 text-sm mb-4">Sube tu nota de voz (boletines, revisiones, cable de 6mm...) y genera el PDF.</p>
            
            <form id="uploadForm" class="flex flex-col gap-4">
                <div class="grid grid-cols-2 gap-3">
                    <div>
                        <label class="text-xs text-slate-400 block mb-1">Nombre Comercial:</label>
                        <input type="text" id="nombreComercial" value="Electricidad Juan" class="w-full bg-slate-900 border border-slate-700 rounded-lg p-2 text-sm text-white" required>
                    </div>
                    <div>
                        <label class="text-xs text-slate-400 block mb-1">Tu Teléfono:</label>
                        <input type="text" id="telefonoAutonomo" value="600123456" class="w-full bg-slate-900 border border-slate-700 rounded-lg p-2 text-sm text-white" required>
                    </div>
                </div>

                <div class="border-2 border-dashed border-slate-600 rounded-xl p-4 text-center hover:border-yellow-400 transition cursor-pointer relative">
                    <input type="file" id="audioFile" accept="audio/*" class="absolute inset-0 opacity-0 cursor-pointer" required>
                    <div id="fileLabel" class="text-slate-300 text-sm font-medium">📁 Toca para seleccionar nota de voz</div>
                </div>

                <button type="submit" id="submitBtn" class="bg-yellow-500 hover:bg-yellow-400 text-slate-950 font-bold py-3 px-4 rounded-xl transition shadow-lg text-sm">
                    ✨ Generar Presupuesto y PDF
                </button>
            </form>
            
            <div id="loading" class="hidden text-center py-6">
                <div class="animate-spin rounded-full h-8 w-8 border-b-2 border-yellow-400 mx-auto mb-2"></div>
                <p class="text-xs text-yellow-300">Analizando jerga eléctrica y creando PDF...</p>
            </div>
        </div>

        <div id="resultadoContainer" class="w-full max-w-lg mt-4 hidden"></div>

        <script>
            let pdfUrlGlobal = "";
            let resumenTextoGlobal = "";

            const fileInput = document.getElementById('audioFile');
            const fileLabel = document.getElementById('fileLabel');
            fileInput.addEventListener('change', (e) => {
                if(e.target.files.length > 0) {
                    fileLabel.textContent = "✔️ " + e.target.files[0].name;
                }
            });

            document.getElementById('uploadForm').addEventListener('submit', async (e) => {
                e.preventDefault();
                const file = fileInput.files[0];
                if(!file) return;

                const formData = new FormData();
                formData.append('file', file);
                formData.append('nombre_comercial', document.getElementById('nombreComercial').value);
                formData.append('telefono_autonomo', document.getElementById('telefonoAutonomo').value);

                document.getElementById('loading').classList.remove('hidden');
                document.getElementById('resultadoContainer').classList.add('hidden');

                try {
                    const response = await fetch('/api/procesar', { method: 'POST', body: formData });
                    const data = await response.json();
                    
                    if(response.ok) {
                        pdfUrlGlobal = data.pdf_url;
                        resumenTextoGlobal = `Hola ${data.presupuesto.cliente}, te adjunto el presupuesto para: ${data.presupuesto.resumen_actuacion}. Total mano de obra: ${data.presupuesto.total_estimado_mano_obra} €.`;

                        let html = `
                            <div class="bg-slate-800 rounded-2xl shadow-xl p-5 border border-slate-700 flex flex-col gap-3 text-sm">
                                <h2 class="text-lg font-bold text-green-400 border-b border-slate-700 pb-2">📋 Presupuesto Revisado</h2>
                                <div><strong class="text-slate-400">Cliente:</strong> ${data.presupuesto.cliente}</div>
                                <div><strong class="text-slate-400">Resumen:</strong> ${data.presupuesto.resumen_actuacion}</div>
                                
                                <div class="bg-slate-900/50 p-3 rounded-xl border border-slate-700">
                                    <div class="font-semibold text-yellow-400 mb-1">🛠️ Mano de Obra / Servicios:</div>
                                    ${data.presupuesto.desglose_lineas.map(l => `<div>▪️ ${l.cantidad}x${l.concepto} -> <span class="text-green-300">${l.subtotal_mano_obra}€</span></div>`).join('')}
                                    <div class="mt-2 font-bold text-right text-yellow-400">Total: ${data.presupuesto.total_estimado_mano_obra} €</div>
                                </div>

                                ${data.presupuesto.observaciones_imprevistos ? `<div class="bg-red-950/40 border border-red-800/60 p-3 rounded-xl text-xs text-red-200"><strong>⚠️ Observaciones / Dudas:</strong> ${data.presupuesto.observaciones_imprevistos}</div>` : ''}

                                <div class="flex gap-2 mt-2">
                                    <a href="${data.pdf_url}" target="_blank" class="flex-1 bg-slate-700 hover:bg-slate-600 text-center py-2.5 px-3 rounded-xl font-bold text-xs border border-slate-600">
                                        📥 Ver / Descargar PDF
                                    </a>
                                    <button onclick="compartirPresupuesto()" class="flex-1 bg-green-600 hover:bg-green-500 text-white text-center py-2.5 px-3 rounded-xl font-bold text-xs shadow">
                                        📤 Enviar por WhatsApp / Mail
                                    </button>
                                </div>
                            </div>
                        `;
                        document.getElementById('resultadoContainer').innerHTML = html;
                        document.getElementById('resultadoContainer').classList.remove('hidden');
                    } else {
                        alert("Error al procesar: " + data.detail);
                    }
                } catch(err) {
                    alert("Error de conexión con el servidor.");
                } finally {
                    document.getElementById('loading').classList.add('hidden');
                }
            });

            async function compartirPresupuesto() {
                if (navigator.share) {
                    try {
                        const response = await fetch(pdfUrlGlobal);
                        const blob = await response.blob();
                        const file = new File([blob], "presupuesto_electricista.pdf", { type: "application/pdf" });

                        if (navigator.canShare && navigator.canShare({ files: [file] })) {
                            await navigator.share({
                                title: 'Presupuesto Electricista',
                                text: resumenTextoGlobal,
                                files: [file],
                            });
                            return;
                        }
                    } catch (err) {
                        console.log("Fallo al compartir fichero directamente.");
                    }
                    
                    navigator.share({
                        title: 'Presupuesto Electricista',
                        text: resumenTextoGlobal + " (Descarga el PDF desde la web)."
                    });
                } else {
                    alert("Tu navegador no soporta compartir nativamente. Usa el botón de ver PDF para descargarlo.");
                }
            }
        </script>
    </body>
    </html>
    """

@app.post("/api/procesar")
async def api_procesar(
    file: UploadFile = File(...),
    nombre_comercial: str = Form(...),
    telefono_autonomo: str = Form(...)
):
    contenido = await file.read()
    
    # Catálogo ampliado de servicios con precios estimados
    catalogo_estimado = {
        "servicios": [
            {"id": "SERV_01", "nombre": "Instalación de punto de luz o enchufe", "precio": 35.00},
            {"id": "SERV_02", "nombre": "Cambio o sustitución de cuadro eléctrico / diferencial", "precio": 120.00},
            {"id": "SERV_03", "nombre": "Tendido de cable adicional (por metro)", "precio": 3.00},
            {"id": "SERV_04", "nombre": "Emisión de Boletín Eléctrico (CIE)", "precio": 150.00},
            {"id": "SERV_05", "nombre": "Revisión y comprobación de la instalación eléctrica", "precio": 60.00},
            {"id": "SERV_06", "nombre": "Instalación y configuración de electrodoméstico", "precio": 45.00}
        ]
    }
    
    resultado = procesar_nota_de_voz_bytes(contenido, file.filename, catalogo_estimado)
    actualizar_inventario_y_generar_cesta(resultado.materiales_a_utilizar)
    
    ruta_pdf = generar_pdf_presupuesto(resultado, nombre_comercial, telefono_autonomo)
    
    return {
        "presupuesto": resultado,
        "pdf_url": f"/descargar-pdf"
    }

@app.get("/descargar-pdf")
def descargar_pdf():
    return FileResponse("presupuesto.pdf", media_type="application/pdf", filename="presupuesto.pdf")