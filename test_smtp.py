import dns.resolver
import smtplib

def check_smtp_mailbox(email: str, host: str):
    try:
        answers = dns.resolver.resolve(host, "MX", lifetime=3.0)
        mx_record = sorted(answers, key=lambda r: r.preference)[0].exchange.to_text()
    except Exception as e:
        print("DNS Error:", e)
        return "unknown"

    try:
        print("Connecting to", mx_record)
        with smtplib.SMTP(mx_record, timeout=3.0) as server:
            server.set_debuglevel(1)
            server.helo("mailcrawl.local")
            c1, m1 = server.mail("test@example.com")
            print("MAIL FROM:", c1, m1)
            code, msg = server.rcpt(email)
            print("RCPT TO:", code, msg)
            if code == 250:
                return "valid"
            elif code >= 500:
                return "invalid"
            return "unknown"
    except Exception as exc:
        print("SMTP Exception:", exc)
        return "unknown"

print("Result:", check_smtp_mailbox("test@google.com", "google.com"))
