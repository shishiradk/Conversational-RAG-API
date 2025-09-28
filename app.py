# app_safe.py

# ---- Fix Intel MKL / Fortran runtime crash ----
import os
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"
os.environ["OMP_NUM_THREADS"] = "1"

# ---- Imports ----
import streamlit as st
import requests
import uuid
from datetime import datetime
from sentence_transformers import SentenceTransformer

API_URL = "http://127.0.0.1:8000"  # Update if FastAPI runs elsewhere

st.set_page_config(page_title="Conversational RAG", layout="wide")
st.title("Conversational RAG Interface (Safe Model Loading)")

# ---------------- Session ----------------
if "session_id" not in st.session_state:
    st.session_state.session_id = str(uuid.uuid4())
if "chat_history" not in st.session_state:
    st.session_state.chat_history = []

# ---------------- Cached Model Loading ----------------
@st.cache_resource(show_spinner=True)
def load_embedding_model():
    return SentenceTransformer("all-MiniLM-L6-v2")

# Load model once; Streamlit caches it
model = load_embedding_model()

# ---------------- Tabs ----------------
tab1, tab2, tab3 = st.tabs(["Upload Document", "Chat", "Book Interview"])

# ---------------- Upload Document ----------------
with tab1:
    st.header("Upload Document")
    uploaded_file = st.file_uploader("Choose a file", type=["txt", "pdf", "docx"])
    
    if uploaded_file:
        with st.spinner("Uploading and processing..."):
            files = {"file": (uploaded_file.name, uploaded_file.getvalue())}
            response = requests.post(f"{API_URL}/upload/", files=files)
        if response.status_code == 200:
            st.success(f"Uploaded {uploaded_file.name}. Chunks added: {response.json().get('chunks_added')}")
        else:
            st.error("Failed to upload file.")

# ---------------- Chat ----------------
with tab2:
    st.header("Chat with Assistant")

    # Display previous chat history
    for entry in st.session_state.chat_history:
        role = entry["role"]
        message = entry["message"]
        timestamp = entry.get("time", "")
        st.markdown(f"**{role}** ({timestamp}): {message}")

    user_input = st.text_input("Type your question here")
    
    if st.button("Send") and user_input:
        data = {"session_id": st.session_state.session_id, "query": user_input}
        with st.spinner("Getting answer..."):
            response = requests.post(f"{API_URL}/chat", data=data)
        if response.status_code == 200:
            answer = response.json().get("answer")
            timestamp = datetime.now().strftime("%H:%M:%S")
            # Save to session_state
            st.session_state.chat_history.append({"role": "User", "message": user_input, "time": timestamp})
            st.session_state.chat_history.append({"role": "Assistant", "message": answer, "time": timestamp})
            st.experimental_rerun()  # Refresh to show full history
        else:
            st.error("Failed to get response from API.")

# ---------------- Book Interview ----------------
with tab3:
    st.header("Book an Interview")
    name = st.text_input("Name")
    email = st.text_input("Email")
    date = st.date_input("Date")
    time = st.time_input("Time")
    
    if st.button("Book"):
        payload = {"name": name, "email": email, "date": str(date), "time": str(time)}
        with st.spinner("Booking interview..."):
            response = requests.post(f"{API_URL}/book_interview", data=payload)
        if response.status_code == 200:
            booking_id = response.json().get("booking_id")
            st.success(f"Interview booked! Booking ID: {booking_id}")
        else:
            st.error("Failed to book interview.")
