import os
import sqlite3
import json
from dotenv import load_dotenv
from google import genai
from fastapi import FastAPI, UploadFile, File, Body
from fastapi.responses import HTMLResponse, FileResponse

# ReportLab para PDFs profesionales
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

# Datos por defecto de la empresa del autónomo
NOMBRE_COMERCIAL_DEFAULT = "Electricidad Juan"
TELEFONO_DEFAULT = "600123456"

# ==========================================
# 1. BASE DE DATOS Y GESTIÓN DE INVENTARIO
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

def actualizar_inventario(materiales_utilizados):
    conexion = sqlite3.connect("electricista.db")
    cursor = conexion.cursor()
    
    for item in materiales_utilizados:
        mat_nombre = item.get("material", "")
        mat_cantidad = item.get("cantidad", 0)
        
        cursor.execute("SELECT nombre_material, stock_actual FROM inventario_furgoneta WHERE nombre_material LIKE ?", (f"%{mat_nombre}%",))
        resultado = cursor.fetchone()
        
        if resultado:
            nombre_db, stock_actual = resultado
            nuevo_stock = stock_actual - mat_cantidad
            cursor.execute("UPDATE inventario_furgoneta SET stock_actual = ? WHERE nombre_material = ?", (nuevo_stock, nombre_db))
            
    conexion.commit()
    conexion.close()

# ==========================================
# 2. PROCESAMIENTO CON GEMINI (IA EXPERTA)
# ==========================================
def procesar_audio_con_gemini(contenido_audio: bytes, nombre_archivo: str, catalogo_precios: dict):
    ruta_temp = f"temp_{nombre_archivo}"
    with open(ruta_temp, "wb") as f:
        f.write(contenido_audio)
        
    audio_file = client.files.upload(file=ruta_temp)

    prompt_sistema = f"""
    Eres el asistente operativo experto de un electricista autónomo. El profesional habla con lenguaje coloquial y directo de la obra.
    
    DICCIONARIO DE JERGA ELÉCTRICA:
    - "El boletín" / "Certificado" -> Certificado de Instalación Eléctrica (CIE).
    - "Cable gordo" / "Cable de cocina" / "Cable para vitro" -> Cable de 6mm.
    - "Automático" / "Magneto" -> Magnetotérmico.
    - "Diferencial" / "El chisme que salta" -> Interruptor diferencial.
    - "Revisar la instalación" / "Revisión" -> Revisión y comprobación de seguridad eléctrica.
    - "Poner electrodoméstico" / "Conectar cocina" -> Instalación y configuración de electrodoméstico.

    Devuelve estrictamente un objeto JSON válido (sin formato markdown ni explicaciones adicionales) con esta estructura exacta:
    {{
      "cliente": "Nombre del cliente detectado",
      "urgencia": "Normal o Urgente",
      "resumen_actuacion": "Resumen técnico de lo realizado",
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
    Compara los servicios con este catálogo oficial de precios estimados: {catalogo_precios}
    """

    # Usamos gemini-1.5-flash por su estabilidad y velocidad en producción
    response = client.models.generate_content(
        model='models/gemini-1.5-flash',
        contents=[audio_file, prompt_sistema],
        config={
            'response_mime_type': 'application/json',
        },
    )
    
    if os.path.exists(ruta_temp):
        os.remove(ruta_temp)
        
    return json.loads(response.text)

# ==========================================
# 3. GENERADOR DE PDF PROFESIONAL
# ==========================================
def generar_pdf_presupuesto(presupuesto, ruta_pdf="presupuesto.pdf"):
    doc = SimpleDocTemplate(ruta_pdf, pagesize=letter, rightMargin=40, leftMargin=40, topMargin=40, bottomMargin=40)
    story = []
    styles = getSampleStyleSheet()
    
    estilo_titulo = ParagraphStyle('TituloEmpresa', parent=styles['Heading1'], fontSize=20, textColor=colors.HexColor("#eab308"), spaceAfter=4)
    estilo_sub = ParagraphStyle('SubHeader', parent=styles['Normal'], fontSize=10, textColor=colors.HexColor("#64748b"), spaceAfter=15)
    estilo_h2 = ParagraphStyle('H2', parent=styles['Heading2'], fontSize=14, textColor=colors.HexColor("#1e293b"), spaceBefore=12, spaceAfter=6)
    estilo_texto = ParagraphStyle('Texto', parent=styles['Normal'], fontSize=10, textColor=colors.HexColor("#334155"), leading=14)
    
    story.append(Paragraph(f"⚡ {NOMBRE_COMERCIAL_DEFAULT}", estilo_titulo))
    story.append(Paragraph(f"Teléfono de contacto: {TELEFONO_DEFAULT}", estilo_sub))
    
    story.append(Paragraph("<b>Datos del Cliente y Servicio</b>", estilo_h2))
    story.append(Paragraph(f"<b>Cliente:</b> {presupuesto.get('cliente', 'Cliente')}", estilo_texto))
    story.append(Paragraph(f"<b>Urgencia:</b> {presupuesto.get('urgencia', 'Normal')}", estilo_texto))
    story.append(Paragraph(f"<b>Actuación:</b> {presupuesto.get('resumen_actuacion', '')}", estilo_texto))
    story.append(Spacer(1, 10))
    
    story.append(Paragraph("<b>Desglose de Mano de Obra y Servicios</b>", estilo_h2))
    data_tabla = [["Concepto", "Cant.", "Precio U.", "Subtotal"]]
    for linea in presupuesto.get('desglose_lineas', []):
        data_tabla.append([
            linea.get('concepto', ''), 
            str(linea.get('cantidad', 1)), 
            f"{linea.get('precio_unitario_mano_obra', 0.0):.2f} €", 
            f"{linea.get('subtotal_mano_obra', 0.0):.2f} €"
        ])
    
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
    
    total_mano_obra = presupuesto.get('total_estimado_mano_obra', 0.0)
    story.append(Paragraph(f"<b>TOTAL ESTIMADO MANO DE OBRA: {total_mano_obra:.2f} €</b>", ParagraphStyle('Total', parent=estilo_texto, fontSize=12, textColor=colors.HexColor("#166534"))))
    
    if presupuesto.get('observaciones_imprevistos'):
        story.append(Spacer(1, 10))
        story.append(Paragraph("<b>Observaciones / Notas:</b>", estilo_h2))
        story.append(Paragraph(presupuesto['observaciones_imprevistos'], estilo_texto))
        
    doc.build(story)
    return ruta_pdf


# ==========================================
# 4. INTERFAZ WEB MÓVIL (FRONTEND LIMPIO)
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
        <title>Presupuestos Flash - Electricista</title>
        <script src="https://cdn.tailwindcss.com"></script>
    </head>
    <body class="bg-slate-900 text-slate-100 min-h-screen p-4 flex flex-col items-center">
        <div class="w-full max-w-md bg-slate-800 rounded-2xl shadow-2xl p-6 border border-slate-700 mt-4 text-center">
            <h1 class="text-2xl font-extrabold text-yellow-400 mb-1 flex items-center justify-center gap-2">⚡ Presupuestos Flash</h1>
            <p class="text-slate-400 text-sm mb-8">Graba tu nota de voz en pie de obra, revisa el desglose y comparte el PDF.</p>
            
            <!-- Botón Grande de Grabación -->
            <div class="flex flex-col items-center gap-4">
                <button id="recordBtn" class="w-28 h-28 bg-red-600 hover:bg-red-500 rounded-full flex items-center justify-center shadow-lg transition transform active:scale-95 border-4 border-red-400/40 cursor-pointer">
                    <svg xmlns="http://www.w3.org/2000/svg" class="h-12 w-12 text-white animate-pulse" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 11a7 7 0 01-7 7m0 0a7 7 0 01-7-7m7 7v4m0 0H8m4 0h4m-4-8a3 3 0 01-3-3V5a3 3 0 116 0v6a3 3 0 01-3 3z" />
                    </svg>
                </button>
                <div id="recordStatus" class="text-sm font-medium text-slate-300">Toca el micrófono para hablar</div>
            </div>
            
            <div id="loading" class="hidden text-center py-6">
                <div class="animate-spin rounded-full h-8 w-8 border-b-2 border-yellow-400 mx-auto mb-2"></div>
                <p class="text-xs text-yellow-300">Procesando audio con IA...</p>
            </div>
        </div>

        <!-- Tarjeta de Revisión Previa -->
        <div id="resultadoContainer" class="w-full max-w-md mt-4 hidden"></div>

        <script>
            let mediaRecorder;
            let audioChunks = [];
            let isRecording = false;
            let presupuestoGlobal = null;
            let pdfUrlGlobal = "";

            const recordBtn = document.getElementById('recordBtn');
            const recordStatus = document.getElementById('recordStatus');

            recordBtn.addEventListener('click', async () => {
                if (!isRecording) {
                    try {
                        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
                        mediaRecorder = new MediaRecorder(stream);
                        audioChunks = [];

                        mediaRecorder.ondataavailable = event => audioChunks.push(event.data);

                        mediaRecorder.onstop = async () => {
                            const audioBlob = new Blob(audioChunks, { type: 'audio/webm' });
                            await enviarAudioAlServidor(audioBlob);
                        };

                        mediaRecorder.start();
                        isRecording = true;
                        recordBtn.classList.remove('bg-red-600', 'hover:bg-red-500');
                        recordBtn.classList.add('bg-slate-700', 'animate-pulse');
                        recordStatus.textContent = "🔴 Grabando... Toca otra vez para parar.";
                    } catch (err) {
                        alert("Permiso de micrófono denegado o no soportado.");
                    }
                } else {
                    mediaRecorder.stop();
                    isRecording = false;
                    recordBtn.classList.remove('bg-slate-700', 'animate-pulse');
                    recordBtn.classList.add('bg-red-600', 'hover:bg-red-500');
                    recordStatus.textContent = "Toca el micrófono para hablar";
                }
            });

            async function enviarAudioAlServidor(blob) {
                const formData = new FormData();
                formData.append('file', blob, 'audio_obra.webm');

                document.getElementById('loading').classList.remove('hidden');
                document.getElementById('resultadoContainer').classList.add('hidden');

                try {
                    const response = await fetch('/api/analizar', { method: 'POST', body: formData });
                    const data = await response.json();
                    
                    if(response.ok) {
                        presupuestoGlobal = data.presupuesto;
                        
                        let html = `
                            <div class="bg-slate-800 rounded-2xl shadow-xl p-5 border border-slate-700 flex flex-col gap-3 text-sm text-left">
                                <h2 class="text-lg font-bold text-green-400 border-b border-slate-700 pb-2">📋 Transcripción y Revisión</h2>
                                <div><strong class="text-slate-400">Cliente:</strong> ${presupuestoGlobal.cliente}</div>
                                <div><strong class="text-slate-400">Urgencia:</strong> ${presupuestoGlobal.urgencia}</div>
                                <div><strong class="text-slate-400">Actuación:</strong> ${presupuestoGlobal.resumen_actuacion}</div>
                                
                                <div class="bg-slate-900/50 p-3 rounded-xl border border-slate-700">
                                    <div class="font-semibold text-yellow-400 mb-1">🛠️ Servicios y Mano de Obra:</div>
                                    ${presupuestoGlobal.desglose_lineas.map(l => `<div>▪️ ${l.cantidad}x${l.concepto} -> <span class="text-green-300">${l.subtotal_mano_obra}€</span></div>`).join('')}
                                    <div class="mt-2 font-bold text-right text-yellow-400">Total: ${presupuestoGlobal.total_estimado_mano_obra} €</div>
                                </div>

                                ${presupuestoGlobal.observaciones_imprevistos ? `<div class="bg-red-950/40 border border-red-800/60 p-3 rounded-xl text-xs text-red-200"><strong>⚠️ Observaciones:</strong> ${presupuestoGlobal.observaciones_imprevistos}</div>` : ''}

                                <button onclick="generarPDFYCompartir()" class="w-full bg-green-600 hover:bg-green-500 text-white font-bold py-3 px-4 rounded-xl transition shadow-lg text-sm mt-2 cursor-pointer">
                                    ✨ OK - Generar PDF y Compartir
                                </button>
                            </div>
                        `;
                        document.getElementById('resultadoContainer').innerHTML = html;
                        document.getElementById('resultadoContainer').classList.remove('hidden');
                    } else {
                        alert("Error al analizar el audio.");
                    }
                } catch(err) {
                    alert("Error de conexión con el servidor.");
                } finally {
                    document.getElementById('loading').classList.add('hidden');
                }
            }

            async function generarPDFYCompartir() {
                document.getElementById('loading').classList.remove('hidden');
                
                try {
                    const response = await fetch('/api/generar-pdf', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(presupuestoGlobal)
                    });
                    const data = await response.json();
                    
                    if(response.ok) {
                        pdfUrlGlobal = data.pdf_url;
                        const textoResumen = `Hola ${presupuestoGlobal.cliente}, te adjunto el presupuesto para: ${presupuestoGlobal.resumen_actuacion}. Total: ${presupuestoGlobal.total_estimado_mano_obra} €.`;
                        
                        if (navigator.share) {
                            try {
                                const pdfResp = await fetch(pdfUrlGlobal);
                                const blob = await pdfResp.blob();
                                const file = new File([blob], "presupuesto.pdf", { type: "application/pdf" });

                                if (navigator.canShare && navigator.canShare({ files: [file] })) {
                                    await navigator.share({ title: 'Presupuesto Eléctrico', text: textoResumen, files: [file] });
                                    return;
                                }
                            } catch(e) {}
                            
                            navigator.share({ title: 'Presupuesto Eléctrico', text: textoResumen + " (Descarga el PDF)." });
                        } else {
                            window.open(pdfUrlGlobal, '_blank');
                        }
                    } else {
                        alert("Error al generar el PDF.");
                    }
                } catch(err) {
                    alert("Error al procesar el PDF.");
                } finally {
                    document.getElementById('loading').classList.add('hidden');
                }
            }
        </script>
    </body>
    </html>
    """

# ==========================================
# 5. ENDPOINTS DE LA API
# ==========================================
@app.post("/api/analizar")
async def api_analizar(file: UploadFile = File(...)):
    contenido = await file.read()
    
    # Catálogo oficial integrado con boletines, revisiones y cable de 6mm
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
    
    resultado = procesar_audio_con_gemini(contenido, file.filename, catalogo_estimado)
    return {"presupuesto": resultado}

@app.post("/api/generar-pdf")
async def api_generar_pdf(presupuesto: dict = Body(...)):
    # Actualizar inventario de furgoneta al confirmar
    actualizar_inventario(presupuesto.get("materiales_a_utilizar", []))
    
    # Generar documento PDF
    generar_pdf_presupuesto(presupuesto)
    
    return {
        "estado": "ok",
        "pdf_url": "/descargar-pdf"
    }

@app.get("/descargar-pdf")
def descargar_pdf():
    return FileResponse("presupuesto.pdf", media_type="application/pdf", filename="presupuesto.pdf")