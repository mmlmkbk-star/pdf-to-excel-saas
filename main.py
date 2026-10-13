from fastapi import FastAPI, UploadFile, File, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
import pdfplumber
import pandas as pd
import io
import sqlite3
import uuid
from pydantic import BaseModel

app = FastAPI()

# تفعيل CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DB_NAME = "users_database.db"

# إنشاء وتحديث جدول المستخدمين
def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            api_key TEXT UNIQUE NOT NULL,
            conversions_used INTEGER DEFAULT 0,
            plan TEXT DEFAULT 'free'
        )
    ''')
    conn.commit()
    conn.close()

init_db()

# موديلات البيانات
class UserRegister(BaseModel):
    email: str
    password: str

class ResetPassword(BaseModel):
    email: str
    new_password: str

class UpgradeRequest(BaseModel):
    email: str

# 1. مسار تسجيل حساب جديد
@app.post("/register")
def register_user(user: UserRegister):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    cursor.execute("SELECT * FROM users WHERE email = ?", (user.email,))
    if cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=400, detail="هذا البريد الإلكتروني مسجل بالفعل!")
    
    new_api_key = f"key_{uuid.uuid4().hex[:16]}"
    
    cursor.execute(
        "INSERT INTO users (email, password, api_key, conversions_used, plan) VALUES (?, ?, ?, 0, 'free')",
        (user.email, user.password, new_api_key)
    )
    conn.commit()
    conn.close()
    
    return {"message": "تم إنشاء الحساب بنجاح!", "api_key": new_api_key, "plan": "free", "conversions_used": 0}

# 2. مسار تسجيل الدخول وإرجاع بيانات اللوحة
@app.post("/login")
def login_user(user: UserRegister):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    cursor.execute("SELECT api_key, conversions_used, plan FROM users WHERE email = ? AND password = ?", (user.email, user.password))
    result = cursor.fetchone()
    conn.close()
    
    if not result:
        raise HTTPException(status_code=401, detail="البريد الإلكتروني أو كلمة المرور غير صحيحة!")
    
    return {
        "message": "تم تسجيل الدخول بنجاح!", 
        "api_key": result[0], 
        "conversions_used": result[1], 
        "plan": result[2]
    }

# 3. مسار إعادة تعيين كلمة السر
@app.post("/reset-password")
def reset_password(data: ResetPassword):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    cursor.execute("SELECT id FROM users WHERE email = ?", (data.email,))
    if not cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=404, detail="البريد الإلكتروني غير مسجل لدينا!")
    
    cursor.execute("UPDATE users SET password = ? WHERE email = ?", (data.new_password, data.email))
    conn.commit()
    conn.close()
    
    return {"message": "تم تحديث كلمة المرور بنجاح! 🔐"}

# 4. مسار ترقية الحساب محلياً (للاختبار والتطوير)
@app.post("/upgrade")
def upgrade_user(data: UpgradeRequest):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    cursor.execute("SELECT id FROM users WHERE email = ?", (data.email,))
    if not cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=404, detail="البريد الإلكتروني غير مسجل لدينا!")
    
    cursor.execute("UPDATE users SET plan = 'pro' WHERE email = ?", (data.email,))
    conn.commit()
    conn.close()
    
    return {"message": "تهانينا! تم ترقية حسابك إلى الباقة الاحترافية Pro بنجاح 💎"}

# 5. مسار Webhook لاستقبال إشعارات الدفع الحقيقية أوتوماتيكياً (من Lemon Squeezy أو Stripe)
@app.post("/webhook/payment-success")
async def payment_webhook(request: Request):
    try:
        data = await request.json()
        # استخراج الإيميل من بيانات منصة الدفع (حسب هيكل Lemon Squeezy / Stripe)
        email = data.get("data", {}).get("attributes", {}).get("user_email")
        
        if email:
            conn = sqlite3.connect(DB_NAME)
            cursor = conn.cursor()
            cursor.execute("UPDATE users SET plan = 'pro' WHERE email = ?", (email,))
            conn.commit()
            conn.close()
            return {"status": "success", "message": "تم ترقية الباقة بنجاح عبر الدفع الإلكتروني"}
            
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
        
    return {"status": "ignored"}

# 6. مسار تحويل الملفات الذكي (مع فحص الحصة والاشتراك)
@app.post("/convert")
async def convert_pdf_to_excel(file: UploadFile = File(...), x_api_key: str = Header(...)):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    cursor.execute("SELECT conversions_used, plan FROM users WHERE api_key = ?", (x_api_key,))
    user = cursor.fetchone()
    
    if not user:
        conn.close()
        raise HTTPException(status_code=401, detail="مفتاح API غير صحيح أو غير مفعل!")
    
    conversions_used, plan = user
    
    # التحقق من القيود المجانية
    if plan == 'free' and conversions_used >= 3:
        conn.close()
        raise HTTPException(
            status_code=403, 
            detail="لقد استهلكت جميع المحاولات المجانية (3/3). يرجى الترقية إلى الباقة الاحترافية Pro للتحويل اللامحدود!"
        )
    
    # معالجة الملف واستخراج الجداول
    contents = await file.read()
    all_tables = []
    
    with pdfplumber.open(io.BytesIO(contents)) as pdf:
        for page in pdf.pages:
            tables = page.extract_tables()
            for table in tables:
                for row in table:
                    clean_row = [cell.replace('\n', ' ') if cell else '' for cell in row]
                    all_tables.append(clean_row)
    
    if not all_tables:
        conn.close()
        raise HTTPException(status_code=400, detail="لم يتم العثور على أي جداول في هذا الملف.")
    
    df = pd.DataFrame(all_tables)
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, header=False)
    output.seek(0)
    
    # زيادة عداد التحويلات
    cursor.execute("UPDATE users SET conversions_used = conversions_used + 1 WHERE api_key = ?", (x_api_key,))
    conn.commit()
    
    cursor.execute("SELECT conversions_used FROM users WHERE api_key = ?", (x_api_key,))
    new_count = cursor.fetchone()[0]
    conn.close()
    
    response = StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=Converted_Table.xlsx"}
    )
    response.headers["X-Conversions-Used"] = str(new_count)
    return response