from pathlib import Path

p = Path("trade_result_monitor.py")
s = p.read_text(encoding="utf-8")

old = '''async def monitor_signal(signal, get_price, send_result):
    wait_seconds = max(1, int(signal["expiry_min"] * 60 - (time.time() - signal["signal_time"])))
    await asyncio.sleep(wait_seconds)
    expiry_price = None
    for attempt in range(4):
        try:
            expiry_price = await get_price(signal["pair"])
        except Exception:
            expiry_price = None
        if expiry_price is not None:
            break
        if attempt < 3:
            await asyncio.sleep(5)
    if expiry_price is None:
        signal["status"] = "UNRESOLVED"
        await send_result(signal, "UNRESOLVED")
        return "UNRESOLVED"
    outcome = evaluate_outcome(signal["entry"], expiry_price, signal["direction"])
    signal["expiry_price"] = float(expiry_price)
    signal["status"] = outcome
    await send_result(signal, outcome)
    return outcome
'''

new = '''async def _deliver_result_with_retry(send_result, signal, outcome, log=None):
    """Deliver an expiry result reliably without ever executing a trade."""
    last_error = None
    for attempt in range(1, 4):
        try:
            sent = await send_result(signal, outcome)
            if sent:
                if log:
                    log.info("SIGNAL RESULT DELIVERY CONFIRMED: pair=%s outcome=%s attempt=%s", signal.get("pair"), outcome, attempt)
                return True
            if log:
                log.warning("SIGNAL RESULT DELIVERY NOT CONFIRMED: pair=%s outcome=%s attempt=%s", signal.get("pair"), outcome, attempt)
        except Exception as exc:
            last_error = exc
            if log:
                log.exception("SIGNAL RESULT DELIVERY ERROR: pair=%s outcome=%s attempt=%s error=%s", signal.get("pair"), outcome, attempt, exc)
        if attempt < 3:
            await asyncio.sleep(2)
    if log:
        log.error("SIGNAL RESULT DELIVERY FAILED AFTER RETRIES: pair=%s outcome=%s error=%s", signal.get("pair"), outcome, last_error)
    return False


async def monitor_signal(signal, get_price, send_result):
    wait_seconds = max(1, int(signal["expiry_min"] * 60 - (time.time() - signal["signal_time"])))
    await asyncio.sleep(wait_seconds)
    expiry_price = None
    for attempt in range(1, 5):
        try:
            expiry_price = await get_price(signal["pair"])
        except Exception:
            expiry_price = None
        if expiry_price is not None:
            break
        if attempt < 4:
            await asyncio.sleep(5)
    if expiry_price is None:
        signal["status"] = "UNRESOLVED"
        await _deliver_result_with_retry(send_result, signal, "UNRESOLVED")
        return "UNRESOLVED"
    outcome = evaluate_outcome(signal["entry"], expiry_price, signal["direction"])
    signal["expiry_price"] = float(expiry_price)
    signal["status"] = outcome
    await _deliver_result_with_retry(send_result, signal, outcome)
    return outcome
'''

if old not in s:
    raise SystemExit("monitor_signal block not found; refusing unsafe edit")
s = s.replace(old, new, 1)

old2 = '''            module.pending_signal_tasks.add(task)
            task.add_done_callback(module.pending_signal_tasks.discard)
            module.log.info("SIGNAL RESULT MONITOR STARTED: pair=%s direction=%s expiry=%s min signal_time=%s task=%s", pair, direction, expiry_text, datetime.now(_UAE).strftime("%H:%M:%S UAE"), getattr(task, "get_name", lambda: "task")())
'''
new2 = '''            module.pending_signal_tasks.add(task)
            def _result_task_done(done_task):
                module.pending_signal_tasks.discard(done_task)
                try:
                    outcome = done_task.result()
                    module.log.info("SIGNAL RESULT MONITOR FINISHED: pair=%s direction=%s outcome=%s task=%s", pair, direction, outcome, getattr(done_task, "get_name", lambda: "task")())
                except asyncio.CancelledError:
                    module.log.error("SIGNAL RESULT MONITOR CANCELLED: pair=%s direction=%s task=%s", pair, direction, getattr(done_task, "get_name", lambda: "task")())
                except Exception as exc:
                    module.log.exception("SIGNAL RESULT MONITOR TASK ERROR: pair=%s direction=%s error=%s", pair, direction, exc)
            task.add_done_callback(_result_task_done)
            module.log.info("SIGNAL RESULT MONITOR STARTED: pair=%s direction=%s expiry=%s min signal_time=%s task=%s", pair, direction, expiry_text, datetime.now(_UAE).strftime("%H:%M:%S UAE"), getattr(task, "get_name", lambda: "task")())
'''
if old2 not in s:
    raise SystemExit("patched_send task block not found; refusing unsafe edit")
s = s.replace(old2, new2, 1)

p.write_text(s, encoding="utf-8")
print("patched trade_result_monitor.py")
