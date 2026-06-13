from flask import Blueprint, jsonify, request

from db.connection import USE_SQLITE, get_conn
from reasoning.engine import run as reasoning_run
from helpers import (
    _execute, _json_val,
    _get_auth_user, _get_config, _parse_json_fields, _validate_apply,
)

bp = Blueprint("apply", __name__)


@bp.get("/api/status")
def public_status():
    conn = get_conn()
    try:
        announced = _get_config(conn, "results_announced", "false") == "true"
        return jsonify({"announced": announced})
    finally:
        conn.close()


@bp.post("/api/apply")
def apply():
    conn = get_conn()
    try:
        user = _get_auth_user(conn)
        if not user:
            return jsonify({"error": "Authentication required."}), 401

        data = request.get_json(silent=True) or {}
        err = _validate_apply(data)
        if err:
            return jsonify({"error": err}), 400

        # Enforce one application per user
        cur = conn.cursor()
        _execute(cur, "SELECT id FROM applications WHERE user_id = %s", (user["id"],))
        if cur.fetchone():
            return jsonify({"error": "You have already submitted an application."}), 409

        facts = {
            "pendapatan_ortu":   int(data["pendapatan_ortu"]),
            "jumlah_tanggungan": int(data["jumlah_tanggungan"]),
            "tagihan_listrik":   int(data["tagihan_listrik"]),
            "wattage_listrik":   int(data["wattage_listrik"]),
            "ipk":               float(data["ipk"]),
            "status_ortu":       data["status_ortu"],
            "pekerjaan_ortu":    data["pekerjaan_ortu"],
            "bantuan_lain":      bool(data.get("bantuan_lain", False)),
        }

        result = reasoning_run(facts)

        # INSERT application — status stays 'pending' until admin announces
        _execute(cur, """
            INSERT INTO applications
                (user_id, nama_pendaftar, pendapatan_ortu, jumlah_tanggungan, tagihan_listrik,
                 wattage_listrik, ipk, status_ortu, pekerjaan_ortu, bantuan_lain, kondisi_khusus)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """, (
            user["id"],
            data["nama_pendaftar"].strip(),
            facts["pendapatan_ortu"], facts["jumlah_tanggungan"],
            facts["tagihan_listrik"], facts["wattage_listrik"],
            facts["ipk"], facts["status_ortu"], facts["pekerjaan_ortu"],
            facts["bantuan_lain"],
            data.get("kondisi_khusus") or None,
        ))

        if USE_SQLITE:
            cur.execute("SELECT last_insert_rowid()")
        else:
            cur.execute("SELECT lastval()")
        app_id = cur.fetchone()[0]

        # INSERT reasoning result (score stored but not revealed until announced)
        _execute(cur, """
            INSERT INTO results
                (application_id, total_skor, skor_per_kategori, reasoning_trace,
                 status_keputusan, is_anomaly, anomaly_reasons)
            VALUES (%s,%s,%s,%s,%s,%s,%s)
        """, (
            app_id,
            result.total_skor,
            _json_val(result.skor_per_kategori),
            _json_val(result.reasoning_trace),
            "pending",
            result.is_anomaly,
            _json_val(result.anomaly_reasons),
        ))
        conn.commit()

        return jsonify({
            "application_id": app_id,
            "message": "Application submitted successfully. Results will be announced by the scholarship committee.",
        })

    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()


@bp.get("/api/result/<int:app_id>")
def get_result(app_id):
    conn = get_conn()
    try:
        announced   = _get_config(conn, "results_announced", "false") == "true"
        admin_view  = request.headers.get("X-Admin-View") == "1"

        cur = conn.cursor()
        _execute(cur, """
            SELECT a.id, a.nama_pendaftar, a.status_aplikasi, a.queue_rank, a.created_at,
                   r.total_skor, r.skor_per_kategori, r.reasoning_trace,
                   r.status_keputusan, r.is_anomaly, r.anomaly_reasons, r.processed_at
            FROM applications a
            JOIN results r ON r.application_id = a.id
            WHERE a.id = %s
        """, (app_id,))
        row = cur.fetchone()
        if not row:
            return jsonify({"error": "Application not found."}), 404

        keys = ["id","nama_pendaftar","status_aplikasi","queue_rank","created_at",
                "total_skor","skor_per_kategori","reasoning_trace",
                "status_keputusan","is_anomaly","anomaly_reasons","processed_at"]
        d = dict(zip(keys, row))
        _parse_json_fields(d)
        d["is_anomaly"] = bool(d["is_anomaly"])
        d["application_id"] = d.pop("id")
        d["announced"] = announced

        if not announced and not admin_view:
            # Hide score and trace from applicants until announcement
            for key in ("total_skor", "skor_per_kategori", "reasoning_trace",
                        "queue_rank", "anomaly_reasons"):
                d[key] = None
            d["is_anomaly"] = False
            d["status_aplikasi"] = "pending"
            d["status_keputusan"] = "pending"

        return jsonify(d)
    finally:
        conn.close()
