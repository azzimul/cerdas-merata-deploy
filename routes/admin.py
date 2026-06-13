import csv
import io
import os
import re

from flask import Blueprint, jsonify, request, Response
from werkzeug.security import generate_password_hash

from db.connection import USE_SQLITE, get_conn
from reasoning.engine import run as reasoning_run
from queue.manager import db_disqualify, db_rank_and_announce, STATUS_QUALIFIED, STATUS_WAITING_LIST
from helpers import (
    _execute, _json_val, _now_iso,
    _get_config, _set_config, _validate_apply,
)

ADMIN_ID   = os.getenv("ADMIN_ID", "admin01")
ADMIN_PASS = os.getenv("ADMIN_PASS", "admin123")

bp = Blueprint("admin", __name__)


def _fill_qualified_slots(conn, cur, quota: int) -> None:
    """Promote waiting_list applicants to fill any open qualified slots up to quota."""
    _execute(cur, "SELECT COUNT(*) FROM applications WHERE status_aplikasi = %s", (STATUS_QUALIFIED,))
    qualified_count = cur.fetchone()[0]

    while qualified_count < quota:
        _execute(cur, """
            SELECT a.id FROM applications a
            JOIN results r ON r.application_id = a.id
            WHERE a.status_aplikasi = %s
            ORDER BY COALESCE(a.queue_rank, 9999) ASC, r.total_skor DESC
            LIMIT 1
        """, (STATUS_WAITING_LIST,))
        row = cur.fetchone()
        if not row:
            break

        promoted_id = row[0]
        _execute(cur, "UPDATE applications SET status_aplikasi = %s, queue_rank = %s WHERE id = %s",
                 (STATUS_QUALIFIED, qualified_count + 1, promoted_id))
        _execute(cur, "UPDATE results SET status_keputusan = %s WHERE application_id = %s",
                 (STATUS_QUALIFIED, promoted_id))
        qualified_count += 1


@bp.get("/api/admin/config")
def admin_config():
    conn = get_conn()
    try:
        announced = _get_config(conn, "results_announced", "false") == "true"
        quota     = int(_get_config(conn, "quota", "50"))
        return jsonify({"announced": announced, "quota": quota})
    finally:
        conn.close()


@bp.post("/api/admin/announce")
def admin_announce():
    conn = get_conn()
    try:
        if _get_config(conn, "results_announced", "false") == "true":
            return jsonify({"error": "Results have already been announced."}), 409

        quota = int(_get_config(conn, "quota", "50"))
        counts = db_rank_and_announce(conn, quota=quota)
        return jsonify({
            "ok": True,
            "qualified":    counts.get("qualified", 0),
            "waiting_list": counts.get("waiting_list", 0),
            "rejected":     counts.get("rejected", 0),
        })
    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()


@bp.post("/api/admin/quota")
def admin_set_quota():
    data = request.get_json(silent=True) or {}
    quota = data.get("quota")
    if quota is None or not str(quota).isdigit() or int(quota) < 1:
        return jsonify({"error": "Quota must be a positive integer."}), 400

    conn = get_conn()
    try:
        if _get_config(conn, "results_announced", "false") == "true":
            return jsonify({"error": "Cannot change quota after results have been announced."}), 409
        _set_config(conn, "quota", str(int(quota)))
        return jsonify({"ok": True, "quota": int(quota)})
    finally:
        conn.close()


@bp.get("/api/admin/applicants")
def admin_list():
    status  = request.args.get("status")
    anomaly = request.args.get("anomaly")

    conn = get_conn()
    try:
        cur = conn.cursor()
        sql = """
            SELECT a.id, a.nama_pendaftar, a.status_aplikasi, a.queue_rank, a.created_at,
                   r.total_skor, r.is_anomaly, r.admin_override, a.kondisi_khusus,
                   r.override_reason, r.disqualify_reason
            FROM applications a
            LEFT JOIN results r ON r.application_id = a.id
            WHERE 1=1
        """
        params = []
        if status:
            sql += " AND a.status_aplikasi = %s"
            params.append(status)
        if anomaly == "1":
            sql += " AND a.kondisi_khusus IS NOT NULL AND a.kondisi_khusus != ''"
        sql += " ORDER BY COALESCE(a.queue_rank, 9999), r.total_skor DESC NULLS LAST" if not USE_SQLITE else \
               " ORDER BY COALESCE(a.queue_rank, 9999), COALESCE(r.total_skor, 0) DESC"

        _execute(cur, sql, params or [])
        rows = cur.fetchall()

        cols = ["id","nama_pendaftar","status_aplikasi","queue_rank","created_at",
                "total_skor","is_anomaly","admin_override","kondisi_khusus",
                "override_reason","disqualify_reason"]
        result = []
        for row in rows:
            d = dict(zip(cols, row))
            d["is_anomaly"] = bool(d["is_anomaly"])
            result.append(d)
        return jsonify(result)
    finally:
        conn.close()


@bp.get("/api/admin/stats")
def admin_stats():
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM applications")
        total = cur.fetchone()[0]

        def count(status):
            _execute(cur, "SELECT COUNT(*) FROM applications WHERE status_aplikasi = %s", (status,))
            return cur.fetchone()[0]

        _execute(cur, "SELECT COUNT(*) FROM appeals WHERE status_banding = %s", ("open",))
        open_appeals = cur.fetchone()[0]

        announced = _get_config(conn, "results_announced", "false") == "true"
        quota     = int(_get_config(conn, "quota", "50"))

        return jsonify({
            "total":        total,
            "pending":      count("pending"),
            "qualified":    count("qualified"),
            "waiting_list": count("waiting_list"),
            "rejected":     count("rejected"),
            "disqualified": count("disqualified"),
            "open_appeals": open_appeals,
            "announced":    announced,
            "quota":        quota,
        })
    finally:
        conn.close()


@bp.post("/api/admin/disqualify/<int:app_id>")
def admin_disqualify(app_id):
    data   = request.get_json(silent=True) or {}
    reason = (data.get("reason") or "").strip()
    if not reason:
        return jsonify({"error": "Disqualification reason is required."}), 400

    conn = get_conn()
    try:
        announced = _get_config(conn, "results_announced", "false") == "true"
        quota     = int(_get_config(conn, "quota", "50"))
        result    = db_disqualify(conn, app_id, reason, ADMIN_ID, quota=quota, announced=announced)
        return jsonify({
            "ok": True,
            "disqualified_id": result.disqualified_id,
            "rank_changes":    len(result.promoted),
            "newly_entered":   result.newly_entered,
        })
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()


@bp.post("/api/admin/override/<int:app_id>")
def admin_override(app_id):
    data   = request.get_json(silent=True) or {}
    reason = (data.get("reason") or "").strip()
    status = data.get("status", "")

    if not reason:
        return jsonify({"error": "Override reason is required."}), 400
    if status not in ("qualified", "waiting_list", "rejected", "disqualified"):
        return jsonify({"error": "Status must be qualified, waiting_list, rejected, or disqualified."}), 400

    conn = get_conn()
    try:
        cur = conn.cursor()
        _execute(cur, "UPDATE applications SET status_aplikasi = %s WHERE id = %s", (status, app_id))
        _execute(cur, """
            UPDATE results
            SET status_keputusan = %s, admin_override = %s, override_reason = %s
            WHERE application_id = %s
        """, (status, True, reason, app_id))

        # If a qualified slot was freed, promote from waiting_list
        if status != "qualified":
            announced = _get_config(conn, "results_announced", "false") == "true"
            if announced:
                quota = int(_get_config(conn, "quota", "50"))
                _fill_qualified_slots(conn, cur, quota)

        conn.commit()
        return jsonify({"ok": True})
    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()


@bp.post("/api/admin/appeal/<int:appeal_id>/resolve")
def admin_resolve_appeal(appeal_id):
    data = request.get_json(silent=True) or {}
    note = (data.get("note") or "").strip()

    conn = get_conn()
    try:
        cur = conn.cursor()
        _execute(cur, """
            UPDATE appeals
            SET status_banding = 'resolved', catatan_admin = %s, resolved_at = %s
            WHERE id = %s
        """, (note or None, _now_iso(), appeal_id))
        conn.commit()
        return jsonify({"ok": True})
    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()


@bp.post("/api/admin/reset")
def admin_reset():
    """Reset all applications for demo purposes. User accounts are preserved."""
    conn = get_conn()
    try:
        cur = conn.cursor()
        # Delete in dependency order to avoid FK violations
        _execute(cur, "DELETE FROM rank_history")
        _execute(cur, "DELETE FROM appeals")
        _execute(cur, "DELETE FROM results")
        _execute(cur, "DELETE FROM applications")
        _execute(cur, "UPDATE system_config SET value = 'false', updated_at = %s WHERE key = 'results_announced'",
                 (_now_iso(),))
        conn.commit()
        return jsonify({"ok": True, "message": "All applications have been reset. User accounts are preserved."})
    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()


@bp.post("/api/admin/import")
def admin_import():
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded."}), 400
    f = request.files["file"]
    if not f.filename.lower().endswith(".csv"):
        return jsonify({"error": "File must be a .csv."}), 400

    IMPORT_PASSWORD = "Demo@1234"
    pw_hash = generate_password_hash(IMPORT_PASSWORD)

    stream  = io.StringIO(f.stream.read().decode("utf-8", errors="replace"))
    reader  = csv.DictReader(stream)

    required_cols = {
        "full_name", "pendapatan_ortu", "jumlah_tanggungan",
        "tagihan_listrik", "wattage_listrik", "ipk",
        "status_ortu", "pekerjaan_ortu", "bantuan_lain",
    }

    conn = get_conn()
    imported = skipped = 0
    errors = []

    try:
        from datetime import datetime, timezone, timedelta

        for row_num, row in enumerate(reader, start=2):
            missing = required_cols - set(row.keys())
            if missing:
                errors.append(f"Row {row_num}: missing columns {missing}")
                skipped += 1
                continue

            full_name = (row.get("full_name") or "").strip()
            if not full_name:
                errors.append(f"Row {row_num}: full_name is empty")
                skipped += 1
                continue

            try:
                facts = {
                    "pendapatan_ortu":   int(row["pendapatan_ortu"]),
                    "jumlah_tanggungan": int(row["jumlah_tanggungan"]),
                    "tagihan_listrik":   int(row["tagihan_listrik"]),
                    "wattage_listrik":   int(row["wattage_listrik"]),
                    "ipk":               float(row["ipk"]),
                    "status_ortu":       row["status_ortu"].strip(),
                    "pekerjaan_ortu":    row["pekerjaan_ortu"].strip(),
                    "bantuan_lain":      row["bantuan_lain"].strip().lower() in ("true", "1", "yes"),
                }
            except (ValueError, KeyError) as e:
                errors.append(f"Row {row_num}: invalid data — {e}")
                skipped += 1
                continue

            err = _validate_apply({**facts, "nama_pendaftar": full_name})
            if err:
                errors.append(f"Row {row_num}: {err}")
                skipped += 1
                continue

            slug     = re.sub(r"[^a-z0-9]+", "_", full_name.lower())[:20].strip("_")
            username = f"{slug}_{row_num:03d}"
            email    = f"{username}@demo.local"

            try:
                cur = conn.cursor()
                _execute(cur, "SELECT id FROM users WHERE username = %s OR email = %s", (username, email))
                existing = cur.fetchone()

                if existing:
                    user_id = existing[0]
                    # Skip only if they already have an application (reset clears apps, so this allows re-import)
                    _execute(cur, "SELECT id FROM applications WHERE user_id = %s", (user_id,))
                    if cur.fetchone():
                        skipped += 1
                        continue
                else:
                    _execute(cur, """
                        INSERT INTO users (username, email, full_name, password_hash)
                        VALUES (%s, %s, %s, %s)
                    """, (username, email, full_name, pw_hash))

                    if USE_SQLITE:
                        cur.execute("SELECT last_insert_rowid()")
                    else:
                        cur.execute("SELECT lastval()")
                    user_id = cur.fetchone()[0]

                result_r = reasoning_run(facts)
                kondisi  = (row.get("kondisi_khusus") or "").strip() or None

                _execute(cur, """
                    INSERT INTO applications
                        (user_id, nama_pendaftar, pendapatan_ortu, jumlah_tanggungan,
                         tagihan_listrik, wattage_listrik, ipk, status_ortu,
                         pekerjaan_ortu, bantuan_lain, kondisi_khusus)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """, (
                    user_id, full_name,
                    facts["pendapatan_ortu"], facts["jumlah_tanggungan"],
                    facts["tagihan_listrik"], facts["wattage_listrik"],
                    facts["ipk"], facts["status_ortu"], facts["pekerjaan_ortu"],
                    facts["bantuan_lain"], kondisi,
                ))

                if USE_SQLITE:
                    cur.execute("SELECT last_insert_rowid()")
                else:
                    cur.execute("SELECT lastval()")
                app_id = cur.fetchone()[0]

                _execute(cur, """
                    INSERT INTO results
                        (application_id, total_skor, skor_per_kategori, reasoning_trace,
                         status_keputusan, is_anomaly, anomaly_reasons)
                    VALUES (%s,%s,%s,%s,%s,%s,%s)
                """, (
                    app_id,
                    result_r.total_skor,
                    _json_val(result_r.skor_per_kategori),
                    _json_val(result_r.reasoning_trace),
                    "pending",
                    result_r.is_anomaly,
                    _json_val(result_r.anomaly_reasons),
                ))

                imported += 1

            except Exception as e:
                errors.append(f"Row {row_num}: {e}")
                skipped += 1
                continue

        conn.commit()
        return jsonify({
            "ok":              True,
            "imported":        imported,
            "skipped":         skipped,
            "errors":          errors[:20],
            "import_password": IMPORT_PASSWORD,
        })

    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()


@bp.get("/api/admin/export")
def admin_export():
    conn = get_conn()
    try:
        cur = conn.cursor()
        sql = """
            SELECT a.id, a.nama_pendaftar, a.pendapatan_ortu, a.jumlah_tanggungan,
                   a.tagihan_listrik, a.wattage_listrik, a.ipk,
                   a.status_ortu, a.pekerjaan_ortu, a.bantuan_lain,
                   a.status_aplikasi, a.queue_rank, a.created_at,
                   r.total_skor, r.is_anomaly, r.admin_override, r.override_reason, r.disqualify_reason
            FROM applications a
            LEFT JOIN results r ON r.application_id = a.id
            ORDER BY COALESCE(a.queue_rank, 9999), a.id
        """
        cur.execute(sql.replace("%s","?") if USE_SQLITE else sql)
        rows = cur.fetchall()
        cols = ["id","name","income","dependents","electricity_bill","wattage",
                "gpa","parent_status","parent_job","other_scholarship",
                "status","rank","applied_at","score","anomaly","override","override_reason","disqualify_reason"]

        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(cols)
        writer.writerows(rows)
        buf.seek(0)

        return Response(
            buf.getvalue(),
            mimetype="text/csv",
            headers={"Content-Disposition": "attachment; filename=cerdas_merata_export.csv"},
        )
    finally:
        conn.close()


@bp.post("/api/admin/login")
def admin_login():
    data = request.get_json(silent=True) or {}
    if data.get("password") == ADMIN_PASS:
        return jsonify({"ok": True})
    return jsonify({"error": "Invalid admin password."}), 401
