import streamlit as st
import requests
from datetime import datetime
import uuid
import re
import dateparser

API_URL = "http://127.0.0.1:8000"  # FastAPI URL

st.set_page_config(page_title="Booking & Chat", layout="wide")

# ------------------------
# Sidebar for page selection
# ------------------------
page = st.sidebar.selectbox("Select Page", ["User Chat & Booking", "Admin"])

# ------------------------
# Session state defaults
# ------------------------
if "session_id" not in st.session_state:
    st.session_state.session_id = str(uuid.uuid4())
if "chat_history" not in st.session_state:
    st.session_state.chat_history = []
if "booking_session" not in st.session_state:
    st.session_state.booking_session = {"name": None, "email": None, "date": None, "time": None, "confirmed": False}
if "greeted" not in st.session_state:
    st.session_state.greeted = False

# ------------------------
# Helpers
# ------------------------
def check_booking_intent(text: str) -> bool:
    keywords = ['book', 'booking', 'appointment', 'schedule', 'meeting', 
                'reservation', 'reserve', 'make an appointment', 'set up',
                'want to book', 'need to book', 'like to book']
    return any(keyword in text.lower() for keyword in keywords)

def extract_name(text: str):
    patterns = [
        r"(?:my name is|i'm|i am|this is|name's|call me)\s+([a-zA-Z\s]+?)(?:\.|,|$|\s+and)",
        r"^([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)(?:\s|$)"
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            name = match.group(1).strip()
            if len(name) > 1 and not any(word in name.lower() for word in ['book', 'appointment', 'schedule']):
                return name.title()
    return None

def extract_email(text: str):
    pattern = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
    match = re.search(pattern, text)
    return match.group(0) if match else None

def extract_datetime(text: str):
    parsed = dateparser.parse(text, settings={'PREFER_DATES_FROM': 'future'})
    if parsed:
        date_str = parsed.date().isoformat()
        time_str = parsed.time().strftime("%H:%M")
        if time_str == "00:00" and not any(word in text.lower() for word in ['midnight', '12am', '00:00']):
            time_str = None
        return date_str, time_str
    return None, None

def format_booking_summary(session):
    return f"""
Please confirm your booking details:
• Name: {session['name']}
• Email: {session['email']}
• Date: {session['date']}
• Time: {session['time']}
Reply with 'yes' or 'confirm' to complete the booking, or tell me what you'd like to change.
"""

# ------------------------
# USER PAGE
# ------------------------
if page == "User Chat & Booking":
    st.title("Booking & Chat App")
    bs = st.session_state.booking_session

    # Booking FORM
    st.subheader("Book Appointment (Form)")
    with st.form("booking_form"):
        form_name = st.text_input("Name", value=bs["name"] or "")
        form_email = st.text_input("Email", value=bs["email"] or "")
        form_date = st.date_input("Date", value=datetime.today() if not bs["date"] else datetime.fromisoformat(bs["date"]))
        form_time = st.time_input("Time", value=datetime.now().time() if not bs["time"] else datetime.strptime(bs["time"], "%H:%M").time())
        submit_form = st.form_submit_button("Book Now")

        if submit_form:
            payload = {
                "name": form_name,
                "email": form_email,
                "date": form_date.isoformat(),
                "time": form_time.strftime("%H:%M")
            }
            try:
                res = requests.post(f"{API_URL}/book/", json=payload, timeout=5)
                if res.status_code == 200:
                    data = res.json()
                    st.success(f"Booking confirmed! ID: {data['booking_id']}")
                    st.session_state.booking_session = {"name": None, "email": None, "date": None, "time": None, "confirmed": False}
                else:
                    st.error(f"Error: {res.text}")
            except Exception as e:
                st.error(f"Server error: {e}")

    st.markdown("---")

    # Chat container
    st.subheader("Chat with Assistant")
    chat_container = st.container()
    for sender, msg in st.session_state.chat_history:
        st.chat_message("user" if sender=="You" else "assistant").write(msg)

    user_input = st.chat_input("Type your message here...")
    if user_input:
        st.session_state.chat_history.append(("You", user_input))
        st.chat_message("user").write(user_input)
        bs = st.session_state.booking_session
        answer = None

        # First greeting
        if not st.session_state.greeted:
            answer = "Hello! How can I help you today?"
            st.session_state.greeted = True

        # Booking flow start
        elif not any(bs.values()) and check_booking_intent(user_input):
            answer = "Sure! Let's book an appointment. What's your name?"

        # Booking flow after started
        elif any(bs.values()) or check_booking_intent(user_input):
            name_extracted = extract_name(user_input)
            email_extracted = extract_email(user_input)
            date_extracted, time_extracted = extract_datetime(user_input)

            if name_extracted and not bs["name"]:
                bs["name"] = name_extracted
            if email_extracted and not bs["email"]:
                bs["email"] = email_extracted
            if date_extracted and not bs["date"]:
                bs["date"] = date_extracted
            if time_extracted and not bs["time"]:
                bs["time"] = time_extracted

            missing = [k for k,v in bs.items() if not v and k!="confirmed"]
            if missing:
                next_field = missing[0]
                prompts = {
                    "name": "Can you tell me your name?",
                    "email": "What's your email address?",
                    "date": "When would you like to schedule the appointment?",
                    "time": "What time works best for you?"
                }
                answer = prompts[next_field]
            elif all([bs["name"], bs["email"], bs["date"], bs["time"]]):
                answer = format_booking_summary(bs)

        # Non-booking fallback
        if not answer:
            try:
                res = requests.post(f"{API_URL}/chat/", json={"session_id": st.session_state.session_id, "message": user_input}, timeout=5)
                answer = res.json().get("answer", "(No response from server)")
            except:
                answer = f"(Fallback) You said: {user_input}"

        st.session_state.chat_history.append(("Assistant", answer))
        st.chat_message("assistant").write(answer)

# ------------------------
# ADMIN PAGE
# ------------------------
if page == "Admin":
    st.title("Admin Panel - Bookings")

    try:
        res = requests.get(f"{API_URL}/all_bookings", timeout=5)
        bookings = res.json() if res.status_code == 200 else {}
    except Exception as e:
        st.error(f"Failed to fetch bookings: {e}")
        bookings = {}

    if bookings:
        for booking_id, data in bookings.items():
            with st.expander(f"Booking ID: {booking_id}"):
                st.write(data)
                if st.button(f"Delete Booking {booking_id}"):
                    try:
                        del_res = requests.delete(f"{API_URL}/bookings/{booking_id}", timeout=5)
                        if del_res.status_code == 200:
                            st.success(f"Deleted booking {booking_id}")
                        else:
                            st.error(f"Error deleting booking {booking_id}")
                    except Exception as e:
                        st.error(f"Server error: {e}")
    else:
        st.info("No bookings found.")
