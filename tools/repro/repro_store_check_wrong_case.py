"""Store check (app.js, v2026.11.334): result of case A shown on case B. Exit 1 = bug."""
import asyncio, sys
page = {"job": "A", "ticket": 1, "status": ""}             # the page's jobId, pageTicket
async def api(state):                                       # mocked GET /api/store/scan
    await asyncio.sleep(0.01); return state
async def store_check(guarded):
    for_job, ticket = page["job"], page["ticket"]
    state = await api({"state": "running"})
    while state["state"] == "running":
        await asyncio.sleep(0.01)
        if for_job != page["job"]: return                   # the only check: before the poll
        state = await api({"state": "done", "matched": 3})  # ...the case can change during it
    if guarded and ticket != page["ticket"]: return         # the fix: after every await
    page["status"] = f"Store check finished: {state['matched']} matched"
async def main(guarded):
    page.update(job="A", ticket=1, status="")
    check = asyncio.create_task(store_check(guarded))
    await asyncio.sleep(0.025); page.update(job="B", ticket=2)  # examiner opens case B
    await check; return page["status"]
shown = asyncio.run(main(guarded="--fixed" in sys.argv))
print(f"case B shows: {shown or '(nothing: the answer for case A was dropped)'}")
sys.exit(1 if shown else 0)
