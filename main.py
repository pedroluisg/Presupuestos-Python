import os
import sqlite3
import json
from dotenv import load_dotenv
from google import genai
from fastapi import FastAPI, UploadFile, File, Body
from fastapi.responses import HTMLResponse, FileResponse
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

NOMBRE_COMERCIAL_DEFAULT = "Electricidad Juan"
TELEFONO_DEFAULT = "600123456"

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
        cursor.executemany("""
            INSERT INTO inventario_furgoneta (nombre_material, stock_actual, stock_minimo)
            VALUES (?, ?, ?)
        """, [
            ("Enchufes", 10, 5),
            ("Cable 1.5mm (metros)", 50, 20),
            ("Cable 2.5mm (metros)", 50, 20),
            ("Cable 6mm (metros)", 30, 10),
            ("Interruptor diferencial", 3, 2),
            ("Magnetotérmico", 4, 2)
        ])
        conexion.commit()
    conexion.close()

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
        <title>Presupuestos Flash</title>
        <script src="https://cdn.tailwindcss.com"></script>
    </head>
    <body class="bg-slate-900 text-slate-100 min-h-screen p-4 flex flex-col items-center">
        <div class="w-full max-w-md bg-slate-800 rounded-2xl shadow-2xl p-6 border border-slate-700 mt-4 text-center">
            <h1 class="text-2xl font-extrabold text-yellow-400 mb-1">⚡ Presupuestos Flash</h1>
            <p class="text-slate-400 text-sm mb-6">Pulsa para grabar la nota de voz y generar el presupuesto.</p>
            
            <button id="recordBtn" class="w-28 h-28 bg-red-600 hover:bg-red-500 rounded-full mx-auto flex items-center justify-center shadow-lg transition cursor-pointer border-4 border-red-400/40">
                <svg xmlns="http://www.w3.org/2000/svg" class="h-12 w-12 text-white animate-pulse" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 11a7 7 0 01-7 7m0 0a7 7 0 01-7-7m7 7v4m0 0H8m4 0h4m-4-8a3 3 0 01-3-3V5a3 3 0 116 0v6a3 3 0 01-3 3z" />
                </svg>
            </button>
            <div id="status" class="text-sm font-medium text-slate-300 mt-4">Toca el micrófono para hablar</div>
            
            <div id="loading" class="hidden text-center py-6">
                <div class="animate-spin rounded-full h-8 w-8 border-b-2 border-yellow-400 mx-auto mb-2"></div>
                <p class="text-xs text-yellow-300">Procesando audio...</p>
            </div>
        </div>

        <div id="resultado" class="w-full max-w-md mt-4 hidden"></div>

        <script>
            let mediaRecorder, chunks = [], recording = false, presupuesto = null;
            const btn = document.getElementById('recordBtn');
            const statusDiv = document.getElementById('status');

            btn.onclick = async () => {
                if (!recording) {
                    try {
                        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
                        mediaRecorder = new MediaRecorder(stream);
                        chunks = [];
                        mediaRecorder.ondataavailable = e => chunks.push(e.data);
                        mediaRecorder.onstop = async () => {
                            const blob = new Blob(chunks, { type: 'audio/webm' });
                            const fd = new FormData();
                            fd.append('file', blob, 'audio.webm');
                            
                            document.getElementById('loading').classList.remove('hidden');
                            document.getElementById('resultado').classList.add('hidden');
                            
                            const res = await fetch('/api/analizar', { method: 'POST', body: fd });
                            const data = await res.json();
                            document.getElementById('loading').classList.add('hidden');
                            
                            if (res.ok) {
                                presupuesto = data.presupuesto;
                                document.getElementById('resultado').innerHTML = `
                                    <div class="bg-slate-800 rounded-2xl shadow-xl p-5 border border-slate-700 text-left text-sm">
                                        <h2 class="text-lg font-bold text-green-400 mb-2">📋 Revisión</h2>
                                        <div><strong>Cliente:</strong> ${presupuesto.cliente}</div>
                                        <div><strong>Actuación:</strong> ${presupuesto.resumen_actuacion}</div>
                                        <div class="mt-2 font-bold text-yellow-400 text-right">Total: ${presupuesto.total_estimado_mano_obra} €</div>
                                        <button onclick="crearPdf()" class="w-full bg-green-600 hover:bg-green-500 text-white font-bold py-3 rounded-xl mt-4 cursor-pointer">📄 Generar PDF y Compartir</button>
                                    </div>`;
                                document.getElementById('resultado').classList.remove('hidden');
                            } else {
                                alert("Error procesando audio");
                            }
                        };
                        mediaRecorder.start();
                        recording = true;
                        btn.className = "w-28 h-28 bg-slate-700 rounded-full mx-auto flex items-center justify-center shadow-lg transition animate-pulse border-4 border-slate-500";
                        statusDiv.textContent = "🔴 Grabando... Toca para parar.";
                    } catch(e) {
                        alert("Sin acceso al micrófono");
                    }
                } else {
                    mediaRecorder.stop();
                    recording = false;
                    btn.className = "w-28 h-28 bg-red-600 hover:bg-red-500 rounded-full mx-auto flex items-center justify-center shadow-lg transition cursor-pointer border-4 border-red-400/40";
                    statusDiv.textContent = "Toca el micrófono para hablar";
                }
            };

            async function crearPdf() {
                const res = await fetch('/api/pdf', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(presupuesto)
                });
                if (res.ok) {
                    window.open('/descargar', '_blank');
                }
            }
        </script>
    </body>
    </html>
    """

@app.post("/api/analizar")
async def api_analizar(file: UploadFile = File(...)):
    contenido = await file.read()
    temp_path = f"temp_{file.filename}"
    with open(temp_path, "wb") as f:
        f.write(contenido)
    
    audio_file = client.files.upload(file=temp_path)
    
    prompt = """
    Eres asistente de electricista. Traduce jerga (boletín = CIE, cable gordo = cable de 6mm, diferencial = diferencial, enchufe = enchufe).
    Devuelve estrictamente un JSON válido con esta estructura exacta:
    {
      "cliente": "Nombre",
      "urgencia": "Normal",
      "resumen_actuacion": "Resumen",
      "desglose_lineas": [{"servicio_id": "S1", "concepto": "Enchufe", "cantidad": 3, "precio_unitario_mano_obra": 35.0, "subtotal_mano_obra": 105.0}],
      "materiales_a_utilizar": [{"material": "Enchufes", "cantidad": 3}],
      "total_estimado_mano_obra": 105.0,
      "tiempo_total_estimado_horas": 2.0,
      "observaciones_imprevistos": null
    }
    """
    
    response = client.models.generate_content(
        model='models/gemini-1.5-flash',
        contents=[audio_file, prompt],
        config={'response_mime_type': 'application/json'}
    )
    
    if os.path.exists(temp_path):
        os.remove(temp_path)
        
    return {"presupuesto": json.loads(response.text)}

@app.post("/api/pdf")
async def api_pdf(presupuesto: dict = Body(...)):
    doc = SimpleDocTemplate("presupuesto.pdf", pagesize=letter, rightMargin=40, leftMargin=40, topMargin=40, bottomMargin=40)
    story = []
    styles = getSampleStyleSheet()
    
    story.append(Paragraph(f"⚡ {NOMBRE_COMERCIAL_DEFAULT}", styles['Heading1']))
    story.append(Paragraph(f"Teléfono: {TELEFONO_DEFAULT}", styles['Normal']))
    story.append(Spacer(1, 10))
    story.append(Paragraph(f"<b>Cliente:</b> {presupuesto.get('cliente')}", styles['Normal']))
    story.append(Paragraph(f"<b>Actuación:</b> {presupuesto.get('resumen_actuacion')}", styles['Normal']))
    story.append(Spacer(1, 10))
    
    data = [["Concepto", "Cant.", "Subtotal"]]
    for l in presupuesto.get('desglose_lineas', []):
        data.append([l.get('concepto'), str(l.get('cantidad')), f"{l.get('subtotal_mano_obra')} €"])
    
    t = Table(data, colWidths=[260, 80, 100])
    story.append(t)
    doc.build(story)
    return {"status": "ok"}

@app.get("/descargar")
def descargar():
    return FileResponse("presupuesto.pdf", media_type="application/pdf", filename="presupuesto.pdf")