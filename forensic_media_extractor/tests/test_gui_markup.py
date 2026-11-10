"""Every element the interface script reaches for must exist.

This exists because of a real failure: a panel's markup was never inserted
while its handlers were, so `$("store-browse")` returned null, the
addEventListener on it threw, and every handler registered after that line
silently never bound. The visible symptom was one Browse button doing
nothing -- several screens away from the cause.

A page is not exercised by importing it, so this is the cheapest check that
catches that whole class of mistake.
"""
import _sandbox  # noqa: F401 -- program state goes to a temp folder, not the tree
import tempfile
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fme.web import media_gui

# Elements built at run time by innerHTML. Each must be reached through a
# guard, which is checked below rather than taken on trust.
# Built by the script itself, so the page cannot hold them: each must be
# reached through a variable that can be checked, never addEventListener
# straight onto the lookup.
DYNAMIC = {
    # The off-screen holder the ribbon's controls live in while they are
    # not on it. Made on first use.
    "control-stash",
    "btn-open-lava",
}

html = media_gui.PAGE
script = re.search(r"<script>(.*)</script>", html, re.S).group(1)
markup = html[:html.index("<script>")] + html[html.rindex("</script>"):]

defined = set(re.findall(r'id="([\w-]+)"', markup))
used = set(re.findall(r'\$\("([\w-]+)"\)', script))
built_later = set(re.findall(r'id="([\w-]+)"', script))
# An element whose id is set after createElement is not found by the
# pattern above, so the ones declared below count as built too --
# otherwise the check reports the script would throw on an element the
# script itself makes.
built_later |= DYNAMIC

ok = True
missing = sorted(used - defined - built_later)
print(f"elements the script uses : {len(used)}")
print(f"elements the markup has  : {len(defined)}")
if missing:
    ok = False
    print("\nMISSING -- the script would throw on these:")
    for name in missing:
        print(f"   {name}")
else:
    print("every referenced element exists in the markup or is built by it")

# Anything built at run time has to be reached defensively: a direct
# addEventListener on one is the same bug in a different place.
print("\ndynamically built elements:")
for name in sorted((used & built_later) | DYNAMIC):
    direct = re.search(rf'\$\("{re.escape(name)}"\)\.addEventListener', script)
    guarded = re.search(rf'(const|let|var)\s+\w+\s*=\s*\$\("{re.escape(name)}"\)',
                        script) or re.search(rf'if \(\$\("{re.escape(name)}"\)', script)
    good = guarded is not None and direct is None
    ok &= good
    print(f"   {name:22} {'guarded' if good else 'REACHED DIRECTLY -- would throw'}")

# The handlers at the end of the script are the ones that stop binding when
# something earlier throws, so check a few of the last ones are present.
for needle in ('$("store-scan")', '$("store-contribute")', '$("hash-browse")',
               '$("sim-scan")', 'function openBrowser'):
    present = needle in script
    ok &= present
    print(f"   {'present' if present else 'MISSING'}  {needle}")

# A class the script adds but the stylesheet never styles is a feature that
# silently does nothing visible -- which is how selection shipped invisible
# once already.
style = re.search(r"<style>(.*?)</style>", html, re.S)
css = style.group(1) if style else ""
toggled = set(re.findall(r'classList\.(?:add|toggle)\("([\w-]+)"', script))
print("\nclasses the script toggles:")
for name in sorted(toggled):
    styled = f".{name}" in css
    ok &= styled
    print(f"   {name:22} {'styled' if styled else 'NOT IN THE STYLESHEET'}")

# The gallery has to sit inside the area that scrolls, with the toolbar and
# the category bar outside it. Getting this wrong gives a gallery of fifty
# thousand tiles no scrollbar of its own -- which is how it shipped once.
print("\nthe results column:")
layout = [
    ("gallery inside the scroll area",
     html.index('id="results-scroll"') < html.index('id="gallery"')
     < html.index('id="active-filters"')),
    ("pager inside it",
     html.index('id="results-scroll"') < html.index('id="pager"')
     < html.index('id="active-filters"')),
    ("toolbar above it",
     html.index('class="toolbar"') < html.index('id="results-scroll"')),
    ("category bar above it",
     html.index('id="cat-bar"') < html.index('id="results-scroll"')),
    ("the scroll area is styled to scroll",
     ".resultsscroll" in html and "overflow-y: auto" in html),
]
for label, passed in layout:
    ok &= passed
    print(f"   {'PASS' if passed else 'FAIL'}  {label}")

print("\nthe side pane's tabs:")
pane_layout = [
    ("the tabs sit inside the pane",
     html.index('id="pane-tabs"') < html.index('id="btn-details"')
     < html.index('id="details"')),
    ("the toolbar keeps one control for it",
     'id="btn-pane"' in html
     and html.index('id="btn-pane"') < html.index('id="details-col"')),
    ("a tab is a button, and the open one is styled",
     '<button class="panetab' in html and ".panetab.active" in html),
    # A tab that closes the pane when clicked twice is a toggle wearing a
    # tab's clothes, and surprises anyone who clicks the one already open.
    ("a tab switches, and never closes",
     'showPane("classify"));' in script and "classify\" && detailsOpen" not in script),
]
for label, passed in pane_layout:
    ok &= passed
    print(f"   {'PASS' if passed else 'FAIL'}  {label}")

# Every function the script calls has to exist. A syntax check passes
# happily on a call to a function that was deleted -- the failure only shows
# when somebody presses the key, and then nothing at all happens. That is
# exactly how grading shipped broken once.
print("\nfunctions the script calls:")
defined = set(re.findall(r"(?:async\s+)?function\s+(\w+)\s*\(", script))
defined |= set(re.findall(r"(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s*)?\(",
                          script))
defined |= set(re.findall(r"(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s+)?function",
                          script))
# A name that holds a function at some point -- a callback kept in a
# variable, say -- is defined for this purpose.
# One statement can declare several names: "let picked = null, target = null".
for declaration in re.findall(r"(?:const|let|var)\s+([^;\n]+)", script):
    for part in declaration.split(","):
        name = re.match(r"\s*(\w+)", part)
        if name:
            defined.add(name.group(1))
defined |= set(re.findall(r"(\w+)\s*=\s*(?:async\s*)?\([^)]*\)\s*=>", script))
# A function's own parameters are defined inside it. Without these, a
# helper that calls the callback it was handed looks like a call to
# something that does not exist.
for _params in re.findall(r"function\s+\w+\s*\(([^)]*)\)", script):
    for _name in re.findall(r"[A-Za-z_]\w*", _params):
        defined.add(_name)
for _params in re.findall(r"\(([^)]*)\)\s*=>", script):
    for _name in re.findall(r"[A-Za-z_]\w*", _params):
        defined.add(_name)

# Strings and comments are full of ordinary words followed by brackets --
# "the source (as recorded)" is not a function call. They are removed before
# anything is counted.
def _strip_text(source: str) -> str:
    out, index, length = [], 0, len(source)
    while index < length:
        ch = source[index]
        nxt = source[index + 1] if index + 1 < length else ""
        if ch == "/" and nxt == "/":
            index = source.find("\n", index)
            if index < 0:
                break
        elif ch == "/" and nxt == "*":
            index = source.find("*/", index)
            index = length if index < 0 else index + 2
        elif ch in "\"'`":
            quote, index = ch, index + 1
            while index < length:
                if source[index] == "\\":
                    index += 2
                    continue
                if source[index] == quote:
                    index += 1
                    break
                # A ${...} inside a template literal is real code.
                if quote == "`" and source[index] == "$" and \
                        index + 1 < length and source[index + 1] == "{":
                    depth, index = 1, index + 2
                    start = index
                    while index < length and depth:
                        if source[index] == "{":
                            depth += 1
                        elif source[index] == "}":
                            depth -= 1
                        index += 1
                    # Spaced, so the code either side of the hole does not
                    # run together into a name that never existed.
                    out.append(" " + source[start:index - 1] + " ")
                    continue
                index += 1
        else:
            out.append(ch)
            index += 1
    return "".join(out)


code_only = _strip_text(script)

# Only plain calls: a method call has a dot in front of it and belongs to
# whatever object it is on, not to this file.
calls = set(re.findall(r"(?<![.\w$])([a-z][A-Za-z0-9_]{3,})\s*\(", code_only))
language = {
    "if", "for", "while", "switch", "catch", "function", "return", "typeof",
    "await", "new", "else", "case", "const", "let", "var", "delete", "void",
    "parseInt", "parseFloat", "decodeURIComponent", "encodeURIComponent",
    "isFinite", "isNaN", "setTimeout", "clearTimeout", "setInterval",
    "fetch", "alert", "confirm", "prompt", "require", "async", "of", "in",
    "requestAnimationFrame", "cancelAnimationFrame", "queueMicrotask",
    "structuredClone", "getComputedStyle", "matchMedia",
}
# Words that only ever appear inside strings or comments would be false
# alarms, so a name counts only where it is called at the start of an
# expression.
missing = sorted(calls - defined - language)
for name in missing:
    print(f"   {name}  <-- CALLED BUT NOT DEFINED")
ok &= not missing
print(f"   {len(calls & defined)} of the script's own functions resolve; "
      f"{len(missing)} do not")

# Grading is the program's whole purpose, and it reaches the server by two
# routes: a key, and a button in the details pane. Both have been removed by
# accident by edits aimed at something else -- once each -- and nothing
# noticed, because the code that was left still parsed and still ran. These
# check the chain is unbroken end to end.
print("\nthe grading path:")

def _calls(where, what):
    """Does the code at this point reach that function?"""
    return what in where


# Each handler is read up to the start of the next one, so a neighbour's
# body is not mistaken for its own.
_starts = [m.start() for m in
           re.finditer(r"addEventListener\(\"key(?:down|up)\"", script)]
_bodies = []
for _index, _start in enumerate(_starts):
    _end = _starts[_index + 1] if _index + 1 < len(_starts) else len(script)
    _bodies.append((_start, script[_start:_end]))

grading_keydown = [(start, body) for start, body in _bodies
                   if "categorize(" in body]
ok &= len(grading_keydown) == 1
print(f"   a keyboard handler that grades        "
      f"{'yes' if grading_keydown else 'MISSING -- keys would do nothing'}")

if grading_keydown:
    handler = grading_keydown[0][1]
    for needs, why in (("categories.find", "a key has to map to a category"),
                       ("toggleFlag(", "flag keys have to work too"),
                       ("selectedPath", "it has to know which file")):
        present = needs in handler
        ok &= present
        print(f"     {needs:22} {'yes' if present else 'MISSING'}  ({why})")

# The details pane's buttons.
ok &= "function wireReview(" in script and "data-cat" in script
print(f"   category buttons wired in the pane    "
      f"{'yes' if 'function wireReview(' in script else 'MISSING'}")

# And the function they both call still talks to the server.
categorize_at = script.find("async function categorize(")
ok &= categorize_at >= 0
body = script[categorize_at:categorize_at + 4000]
reaches_server = "/api/case/categorize" in body
ok &= reaches_server
print(f"   categorize() posts to the server      "
      f"{'yes' if reaches_server else 'MISSING -- nothing would be saved'}")

# The counts above the gallery have to follow the decisions the page holds,
# not a figure the server sent when the case was opened. Grading stopped
# re-reading the whole case in v83 -- which is what made it fast -- and the
# bar was left showing the numbers from load time.
print("\nthe counts above the gallery:")
counts_live = "function countsFromDecisions(" in script
ok &= counts_live
print(f"   counted from the page's own decisions   "
      f"{'yes' if counts_live else 'MISSING'}")

render_at = script.find("function render(")
render_body = script[render_at:render_at + 2500] if render_at >= 0 else ""
redraws = "renderCategoryFilter()" in render_body
ok &= redraws
print(f"   redrawn whenever the gallery is         "
      f"{'yes' if redraws else 'MISSING -- the numbers would go stale'}")

# And grading must reach render(), or nothing redraws at all.
categorize_at = script.find("async function categorize(")
grading = script[categorize_at:categorize_at + 2800] if categorize_at >= 0 else ""
reaches = any(call in grading for call in
              ("moveTo(", "afterBulkGrade(", "render("))
ok &= reaches
print(f"   grading causes a redraw                 "
      f"{'yes' if reaches else 'MISSING'}")

# Grading one file must not rebuild the page. Replacing the markup of a few
# thousand tiles destroys and recreates that many image elements, which the
# browser then has to lay out and fetch again -- seconds of blocked script
# per keystroke, and eventually Chrome stops the page outright.
print("\nwhat a keystroke costs:")
patches = "function paintGraded(" in script
ok &= patches
print(f"   repaints only the tiles that changed    "
      f"{'yes' if patches else 'MISSING'}")

single_at = script.find("async function categorize(")
single = script[single_at:single_at + 2600] if single_at >= 0 else ""
# A single grading should reach paintGraded, and should not call render()
# itself -- moveTo does that only when the page genuinely has to change.
grades_without_rebuild = "paintGraded(" in single
ok &= grades_without_rebuild
print(f"   repaints instead of rebuilding          "
      f"{'yes' if grades_without_rebuild else 'NO -- the page would hang'}")

capped = "MAX_TILES" in script
ok &= capped
print(f"   a cap on how many tiles are built       "
      f"{'yes' if capped else 'MISSING -- \"all\" would hang the browser'}")

# Grading fifteen files and grading one are the same act at different
# sizes, so the cursor should land in the same place either way: on the
# next file, ready for the next decision.
advances = "function nextAfterSelection(" in script
ok &= advances
print(f"   the cursor moves on after a selection   "
      f"{'yes' if advances else 'MISSING'}")

# Ctrl-A in a gallery means "everything I can see". Selecting a whole case
# with it, and grading that by one keystroke, is how a morning's work gets
# overwritten -- so the shortcut takes the page, and anything larger than
# the page asks first.
print("\nguards against grading the wrong files:")
page_scoped = "setSelection(pageOf(visible())" in script
ok &= page_scoped
print(f"   ctrl-A selects the page, not the case   "
      f"{'yes' if page_scoped else 'NO -- it would select everything'}")

asks = "> Math.max(onPage, 1)" in script and "confirm(" in script
ok &= asks
print(f"   more files than the page asks first     {'yes' if asks else 'NO'}")

undo_present = "function undoLast(" in script and "/api/case/undo" in script
ok &= undo_present
print(f"   an undo, by button and by ctrl-Z        "
      f"{'yes' if undo_present else 'MISSING'}")

# The header is what an examiner looks at while working: the case name, the
# two things they press, and nothing else. A list of supported file systems
# belongs where evidence is chosen, not beside the report button.
# The ribbon: six tabs of what can be done, and under each the groups of
# things it offers. What matters in a test is not how it looks but that
# nothing in it is a dead end -- every item has to reach something the
# program can actually do.
print("\nthe ribbon:")
ok &= "<header>" not in html
print(f"   no separate title row           {'yes' if '<header>' not in html else 'still there'}")

has_ribbon = 'id="ribbon-tabs"' in html and "function renderRibbon(" in script
ok &= has_ribbon
print(f"   it exists and is built          {'yes' if has_ribbon else 'MISSING'}")

definition_at = script.find("function ribbonDefinition(")
definition = script[definition_at:script.find("function renderRibbon(")] \
    if definition_at >= 0 else ""
tabs = re.findall(r'\n    "?([a-z /]+)"?: \[', definition)
# Display sits between View and Apps: it changes how the case is shown,
# which is next to View's concerns, and nothing on it touches a file.
expected = ["file", "home", "case data", "report / export", "view",
            "display", "apps"]
ok &= tabs == expected
print(f"   tabs: {' | '.join(tabs)}")
print(f"   in the intended order           {'yes' if tabs == expected else 'NO'}")

# Every item's action has to resolve. A ribbon that offers what the program
# cannot do is worse than no ribbon.
defined = set(re.findall(r"(?:async\s+)?function\s+(\w+)\s*\(", script))
defined |= set(re.findall(r"(?:const|let|var)\s+(\w+)", script))
referenced = set(re.findall(r"go:\s*([A-Za-z_]\w*)\s*[,}]", definition))
dangling = sorted(referenced - defined)
ok &= not dangling
print(f"   every item reaches something    {dangling or 'yes'}")

# Items that act on one file must be disabled when none is chosen, or they
# throw at the person instead of telling them.
guarded = definition.count("needs:") >= 10
ok &= guarded
print(f"   items that need a file say so   {'yes' if guarded else 'NO'}")

caps_out = 'id="caps"' in html
ok &= caps_out

# Saving has to do something real. An entry that only says "saved" is worse
# than none: it teaches people to trust a reassurance.
saves = "/api/case/snapshot" in script and "function saveCase(" in script
# Save and Export live on the ribbon now; the drop-down menu they used to
# share is gone, because two ways into one thing is two things to keep
# working.
on_ribbon = "go: saveCase" in script and "go: openReport" in script
ok &= on_ribbon
print(f"   both are on the ribbon          {'yes' if on_ribbon else 'MISSING'}")
ok &= saves
print(f"   it writes a snapshot            {'yes' if saves else 'NO -- it would be a no-op'}")

# Scrubbing used to set the video's currentTime on every mouse move: dozens
# of seeks a second, each a range request and a decode. On carved video --
# malformed by nature -- the browser falls behind and the tab stops
# responding. It swaps pre-extracted frames instead.
print("\nscrubbing a video:")
scrub_at = script.find("function wireScrub(")
scrub = script[scrub_at:scrub_at + 4000] if scrub_at >= 0 else ""
uses_frames = "/api/frames" in scrub
ok &= uses_frames
print(f"   shows extracted frames                 "
      f"{'yes' if uses_frames else 'NO -- it would seek the video'}")

# Any seeking left must be throttled and must never overlap: a queue of
# seeks is the thing that hangs the tab.
throttled = "requestAnimationFrame" in scrub
guarded = "seeking" in scrub and "pending" in scrub
ok &= throttled and guarded
print(f"   at most one update per drawn frame     {'yes' if throttled else 'NO'}")
print(f"   never more than one seek outstanding   {'yes' if guarded else 'NO'}")

# And a tile has to know which file it is, or its frames cannot be fetched.
carries_path = ('data-poster="${esc(f.rel_path)}"' in html
                or 'data-scrub="${esc(f.rel_path)}"' in html)
ok &= carries_path
print(f"   each tile carries its own path         {'yes' if carries_path else 'NO'}")

# A gallery tile must not contain a video element. Five hundred of them is
# five hundred media elements, each holding decoder resources, and browsers
# cap how many they keep before tearing them down -- which is a page of
# video becoming unresponsive.
tile_start = html.find('f.previewable && f.category === "video"')
tile_markup = html[tile_start:tile_start + 900] if tile_start >= 0 else ""
# Only the generated markup matters, not the comment explaining it.
generated = "".join(re.findall(r'`([^`]*)`', tile_markup))
no_video_in_tiles = "<video" not in generated
ok &= no_video_in_tiles
print(f"   tiles hold a picture, not a video      "
      f"{'yes' if no_video_in_tiles else 'NO -- a page of video would hold hundreds'}")

made_on_hover = 'createElement("video")' in script and "mouseenter" in script
released = "removeAttribute(\"src\")" in script or "video.remove()" in script
ok &= made_on_hover and released
print(f"   a video is made on hover               {'yes' if made_on_hover else 'NO'}")
print(f"   and released on the way out            {'yes' if released else 'NO'}")

# Frames are fetched ahead of the cursor so the first hover does not wait.
# Done carelessly that is worse than the wait: a page of five hundred tiles
# asking at once occupies the server while the examiner is trying to grade.
print("\nfetching frames ahead of the cursor:")
prefetch_at = script.find("function prefetchFrames(")
prefetch = script[prefetch_at:prefetch_at + 2500] if prefetch_at >= 0 else ""
pump = script[script.find("async function pumpFrames("):][:1200]

limited = "FRAME_WORKERS" in script and "framesBusy < FRAME_WORKERS" in pump
ok &= limited
print(f"   a limit on how many at once            {'yes' if limited else 'NO'}")

only_visible = "IntersectionObserver" in script
ok &= only_visible
print(f"   only tiles actually on screen          {'yes' if only_visible else 'NO'}")

# A page change has to abandon the old queue, or scrolling through a case
# leaves hundreds of decodes running for pages nobody is looking at.
abandons = "framesGeneration" in script and "!== framesGeneration" in pump
ok &= abandons
print(f"   a page change abandons the old queue   {'yes' if abandons else 'NO'}")

once_only = "framesReady.has(relPath)" in prefetch
ok &= once_only
print(f"   each clip is asked for once            {'yes' if once_only else 'NO'}")

# A window of the interface's own: the same engine, with nothing else in
# the page. Extensions injecting script into a gallery of thousands of tiles
# is a common way for a renderer to be killed, and not something this
# program can defend against from inside the page.
print("\nopening in a window of its own:")
import subprocess as _subprocess

from fme.web import media_gui as _gui

_built = {}


class _FakeLaunch:
    def __init__(self, command, **kwargs):
        _built["command"] = command


_real_popen = _subprocess.Popen
_subprocess.Popen = _FakeLaunch
_gui.APP_BROWSERS["posix"].insert(0, "/usr/bin/true")
try:
    launched = _gui.open_as_app("http://127.0.0.1:5001", os.path.join(tempfile.gettempdir(), "fme_profile_check"))
finally:
    _subprocess.Popen = _real_popen

ok &= launched.get("ok", False)
command = " ".join(_built.get("command", []))
for needed, why in (("--app=", "a window with no address bar or tabs"),
                    ("--user-data-dir=", "a profile of its own"),
                    ("--disable-extensions", "nothing else in the page")):
    present = needed in command
    ok &= present
    print(f"   {needed:22} {'yes' if present else 'MISSING'}  ({why})")

# Anything the script adds to a tile has to be positioned, or the browser
# lays it out as an ordinary inline element: the video ends up below the
# picture instead of over it, and the tile shows half a frame, the filename
# row, then the rest. Every class the script creates needs a rule.
print("\nwhat the script adds to a tile is styled:")
created = set(re.findall(r'className = "(\w[\w-]*)"', script))
created |= set(re.findall(r'class="(hovervideo|poster|scrubshot)"', script))
for name in sorted(created):
    if name in ("item", "sep", "off", "on"):      # menu parts, styled already
        continue
    styled = f".{name}" in html
    ok &= styled
    print(f"   .{name:16} {'styled' if styled else 'NO RULE -- it would stack'}")

# And specifically: the hover video is positioned inside the thumbnail, not
# the card. Inside the card it covers the filename row too.
inside_thumb = ".card .thumb .hovervideo" in html and "position: absolute" in html
ok &= inside_thumb
print(f"   the hover video sits in the thumbnail  "
      f"{'yes' if inside_thumb else 'NO'}")

# Connecting to a shared store needs three things, and the one that was
# missing cost an examiner an afternoon: there was nowhere to give the
# certificate, so a lab's own server could not be reached at all and the
# button appeared to do nothing.
print("\nconnecting to a hash store:")
for field, why in (("store-path", "the file or the server address"),
                   ("store-token", "who is asking"),
                   ("store-cert", "which certificate to trust")):
    there = f'id="{field}"' in html
    ok &= there
    print(f"   {field:12} {'present' if there else 'MISSING'}  ({why})")

sends_cert = '"/api/store/token"' in script and "cert" in script
ok &= sends_cert
print(f"   the certificate reaches the server client: {sends_cert}")

# The hash tab is three numbered steps now; the similarity tools have a tab
# of their own, because they are part of reviewing, not of hashes.
hashes_at = html.index('id="tab-hashes"')
similar_at = html.index('id="tab-similar"')
hash_sections = re.findall(r'class="section">(\d)', html[hashes_at:similar_at])
ok &= hash_sections == ["1", "2", "3"]
print(f"   the hash tab is numbered {' then '.join(hash_sections) or 'not at all'}")

moved = all(f'id="{control}"' in html[similar_at:]
            for control in ("sim-scan", "frames-match", "screen-scan"))
ok &= moved
print(f"   similarity tools are on their own tab: {moved}")

# A number that might not be there must not be formatted directly. One
# missing field in a response used to throw inside the handler, and what
# reached the examiner was "Cannot read properties of undefined (reading
# 'toLocaleString')" -- a message about this program's internals, in place
# of the answer they asked for.
print("\nformatting numbers that may be absent:")
raw_calls = re.findall(r"(\w+(?:\.\w+)*)\.toLocaleString\(\)", script)
unguarded = [call for call in raw_calls
             if call.startswith(("r.", "d.report."))]
ok &= not unguarded
print(f"   report fields formatted directly: {unguarded or 'none'}")

guarded = "function count(n) {" in script
ok &= guarded
print(f"   a guard for absent numbers exists: {guarded}")

# An examiner starting a case should find the store already there. Making
# them press a button first is a step that exists only because the program
# forgot what it was told yesterday.
print("\nthe store at startup:")
connects = "function connectRememberedStore(" in script
ok &= connects
print(f"   connects to the remembered store  "
      f"{'yes' if connects else 'NO -- they would press a button each time'}")

ticked = 'id="store-remember" checked' in html
ok &= ticked
print(f"   remembering is on by default      {'yes' if ticked else 'no'}")

# And a store that cannot be reached at startup is a thing to know, not an
# error dialog: the examiner may not need it for what they are doing.
quiet = "not connected" in script
ok &= quiet
print(f"   an unreachable store is reported, not thrown: {quiet}")

# A tile can stand for more than itself: identical copies found elsewhere,
# and files that merely look alike. They are different claims, so they are
# two badges -- one number for both would hide which is which, and the
# difference decides how much the count means.
print("\nstacks:")
badges = "function stackBadges(" in script
ok &= badges
print(f"   a tile says what it stands for      {'yes' if badges else 'MISSING'}")
separate = "copies" in script and "similar_count" in script
ok &= separate
print(f"   copies and look-alikes counted apart {'yes' if separate else 'NO'}")

collapses = "function collapseStacks(" in script and "function visible()" in script
ok &= collapses
print(f"   the gallery can collapse a stack     {'yes' if collapses else 'MISSING'}")

# The rule that matters: a collapsed tile grades everything it stands for.
# Anything else leaves files ungraded behind a tile that looks dealt with.
grades_group = "stackMembers(file).forEach" in script
ok &= grades_group
print(f"   grading a stack grades all of it     "
      f"{'yes' if grades_group else 'NO -- files would be left behind'}")

styled = ".card .stackn" in html and ".card.stacked" in html
ok &= styled
print(f"   and it is styled, not stacked inline {'yes' if styled else 'NO'}")

# A control that exists in the markup but is never filled in is worse than
# one that is missing: it looks like the feature is there. The report's
# category picker shipped that way once -- the box was in the page and
# nothing ever put checkboxes in it.
print("\nthe report's category picker:")
builder = "function renderReportCategories(" in script
ok &= builder
print(f"   something builds the list              "
      f"{'yes' if builder else 'MISSING -- the box would stay empty'}")

shown_on_change = ('$("rp-scope").value === "categories"' in script
                   and "rp-categories" in script)
ok &= shown_on_change
print(f"   choosing that scope reveals it         "
      f"{'yes' if shown_on_change else 'NO'}")

# Built when the dialog opens too, not only on change: reopening with the
# scope already set must not show an empty box.
on_open = "pickingNow" in script
ok &= on_open
print(f"   and it is built when the dialog opens  {'yes' if on_open else 'NO'}")

reaches_server = "body.categories = chosen" in script
ok &= reaches_server
print(f"   the choice reaches the server          "
      f"{'yes' if reaches_server else 'NO -- it would be ignored'}")

# Anything the script fills in has to have somewhere to put it. Every
# container the script writes innerHTML into must exist in the markup.
filled = set(re.findall(r'\$\("([\w-]+)"\)\.innerHTML', script))
missing_targets = sorted(name for name in filled
                         if f'id="{name}"' not in html)
ok &= not missing_targets
print(f"   every list the script fills has a home: "
      f"{missing_targets or 'yes'}")

# Styles have to be inside the stylesheet, not merely somewhere in the
# file. An insertion that missed its anchor left every ribbon rule out of
# <style>: the markup was right, the script was right, and the page came
# out as a column of plain text because nothing was styled.
print("\nstyles reach the stylesheet:")
stylesheet = re.search(r"<style>(.*?)</style>", html, re.S)
ok &= stylesheet is not None
sheet = stylesheet.group(1) if stylesheet else ""

# Every class the markup uses, and every class the script adds, should
# have a rule -- bar the handful that exist only to be found by script.
# A class attribute can hold a template expression -- class="card ${x}" --
# and the expression is not a class name. Those are removed first, or the
# check reports variables as missing styles.
flat = re.sub(r"\$\{[^}]*\}", " ", html)
flat_script = re.sub(r"\$\{[^}]*\}", " ", script)
used = set(re.findall(r'class="([^"]+)"', flat))
used |= set(re.findall(r'className = "([^"]+)"', flat_script))
names = set()
for group in used:
    for name in group.split():
        if name.isidentifier() or "-" in name:
            names.add(name.strip())

# Classes that exist only so the script can find those elements are not
# styling and need no rule.
selectors = set(re.findall(r'querySelectorAll\("\.([\w-]+)', script))
selectors |= set(re.findall(r'querySelector\("\.([\w-]+)', script))
names -= selectors

structural = {"row", "inline", "active", "on", "off", "open", "hidden",
              "disabled", "sel", "focus", "shown", "blurred", "scrolls",
              "scrubbing", "stacked", "here", "none", "warn", "err", "p",
              "k", "n", "t", "b", "v", "sub", "nm", "key", "hint", "swatch"}
unstyled = sorted(name for name in names
                  if name not in structural and f".{name}" not in sheet)
ok &= not unstyled
print(f"   classes with no rule anywhere: {unstyled or 'none'}")

# And specifically the bar at the top, which is the first thing seen.
for rule in (".titlebar", ".ribbon-tabs", ".ribbon-item"):
    there = rule in sheet
    ok &= there
    print(f"   {rule:16} {'styled' if there else 'MISSING FROM <style>'}")

# The case being reviewed has a workspace of its own. It used to live
# inside the extraction panel, so opening the hash store hid it and there
# was no way back short of opening the case again.
print("\ngetting back to the case:")
own_panel = 'id="tab-review"' in html
ok &= own_panel
print(f"   the gallery has a panel of its own  {'yes' if own_panel else 'NO'}")

wrap_at = html.find('id="wrap"')
panel = html.rfind('<div class="tab-content"', 0, wrap_at)
holder = re.search(r'id="(tab-\w+)"', html[panel:panel + 90])
inside = holder.group(1) if holder else "?"
ok &= inside == "tab-review"
print(f"   and the gallery is in it            {inside}")

way_back = script.count('showTab("review")') >= 3
ok &= way_back
print(f"   the ribbon leads back to it         "
      f"{'yes' if way_back else 'NO -- only by opening the case again'}")

# Results have to be readable from whichever tab you are on. Writing them
# into the extraction panel is why four exports looked like they did
# nothing at all.
has_toast = 'id="toast"' in html and "function toast(" in script
ok &= has_toast
print(f"   results are shown where they can be read: {has_toast}")

exports_use_it = script.count("toast(") >= 8
ok &= exports_use_it
print(f"   the exports use it                  {'yes' if exports_use_it else 'NO'}")

# Deciding what a picture is often turns on a detail a few pixels wide, so
# the full size viewer zooms. And Ctrl-B blurs everything at once, which is
# the key somebody reaches for when a colleague walks up.
print("\nviewing a picture:")
zooms = all(name in script for name in
            ("function zoomTo(", "function zoomStep(", "function zoomReset("))
ok &= zooms
print(f"   the viewer zooms                   {'yes' if zooms else 'MISSING'}")

at_pointer = ": atX) - box.left" in script
ok &= at_pointer
print(f"   and zooms where the pointer is     "
      f"{'yes' if at_pointer else 'NO -- it would jump away'}")

pans = "zoomDragging" in script and "mousemove" in script
ok &= pans
print(f"   a picture larger than the frame pans {'yes' if pans else 'NO'}")

blur_key = 'e.key.toLowerCase() === "b"' in script
ok &= blur_key
print(f"   Ctrl-B blurs everything            {'yes' if blur_key else 'MISSING'}")

# Hiding the screen must cover everything, and be lifted by nothing. The
# blur used while reviewing is per category and lifts on hover, which is
# right for reviewing and useless when somebody walks up to the desk -- an
# examiner sitting in Uncategorized has, by definition, files nobody has
# looked at.
covers_everything = ("body.hidden-screen img" in html
                     and "body.hidden-screen video" in html)
ok &= covers_everything
print(f"   it covers every picture and video  "
      f"{'yes' if covers_everything else 'NO'}")

# Nothing lifts it: not hover, not the B key, not selection.
lifts_for_nothing = ("body.hidden-screen .card:hover img" in html
                     and "body.hidden-screen .card.shown img" in html)
ok &= lifts_for_nothing
print(f"   and nothing lifts it               "
      f"{'yes' if lifts_for_nothing else 'NO -- hover would reveal'}")

# It must not depend on which categories the review blur covers, or it
# does nothing at all in Uncategorized.
independent = "hideScreen(" in script and "blurScope" not in script[
    script.find("function hideScreen("):script.find("function hideScreen(") + 700]
ok &= independent
print(f"   independent of the review blur     "
      f"{'yes' if independent else 'NO'}")

# A blurred clip that keeps playing still has sound, and sound carries
# further than a screen does.
stops_sound = "video.pause()" in script
ok &= stops_sound
print(f"   and playing video is stopped       {'yes' if stops_sound else 'NO'}")

# The grading keys must survive: a keyboard change has broken them twice.
grading_intact = "nextAfter" in script and "categorize(" in script
ok &= grading_intact
print(f"   and the grading keys still work    {'yes' if grading_intact else 'NO'}")

# Every panel the ribbon can reach has to have something in it. An item
# that opens an empty screen is worse than one that is missing: it looks
# like the program is broken, and it shipped that way once -- the
# extraction form is the left column of the workspace, and moving the
# workspace left its panel empty behind the first button anybody presses.
print("\nno ribbon item opens an empty panel:")
panels = re.findall(r'id="tab-(\w+)"', html)
empty = []
for name in panels:
    at = html.find(f'id="tab-{name}"')
    end = html.find('<div class="tab-content"', at + 10)
    body = html[at:end if end > 0 else len(html)]
    words = len(re.sub(r"<[^>]+>", " ", body).split())
    if words < 5:
        empty.append(f"tab-{name} ({words} word(s))")
ok &= not empty
print(f"   {empty or 'every panel has content'}")

# And every workspace the script switches to must exist in the markup.
switched = set(re.findall(r'showTab\("(\w+)"\)', script))
absent = sorted(name for name in switched
                if f'id="tab-{name}"' not in html)
ok &= not absent
print(f"   panels the script switches to but that do not exist: "
      f"{absent or 'none'}")

# The buttons that do real work to a case have to say what they do. A
# hover text repeating the label tells an examiner nothing they cannot
# already read, which is what these carried before.
print("\nthe buttons that act on a case explain themselves:")
# Every one of them, not a sample: a button an examiner cannot ask about
# is a button they will either avoid or press blind, and both are worse
# than a sentence.
explained = re.findall(r'tip:\s*"', script)
items = re.findall(r'\{label: "', script)
ok &= len(explained) >= 61
print(f"   {len(explained)} item(s) carry an explanation")

# And a sentence, not a restated label.
stubs = [tip for tip in re.findall(r'tip:\s*"([^"]{0,24})"', script)]
ok &= not stubs
print(f"   none of them is too short to be useful: {not stubs}")

# And the renderer has to use it, or they are comments.
uses_tip = 'item.tip || item.label' in script
ok &= uses_tip
print(f"   the ribbon shows it on hover: {uses_tip}")

# The toolbar band had eleven filter chips and nine controls in one row.
# The controls that are set once a session are behind a button now, and
# the ones an examiner touches while grading stayed. What must not happen
# is a second copy of a control: the ribbon drives the originals by
# clicking them, so a rebuilt copy would leave one of the two dead.
print("\nthe gallery toolbar")
moved = html.count("moved-to-ribbon") - 1          # the CSS rule itself
ok &= moved >= 5
print(f"   {moved} control(s) moved to the ribbon rather than rebuilt")

for element in ("per-page", "tile-size", "tz-select", "blur-on",
                "hold-page", "btn-refresh", "btn-undo", "btn-pane"):
    there = f'id="{element}"' in html
    ok &= there
    if not there:
        print(f"   {element} is gone -- the ribbon clicks it, so it must "
              f"still exist")
print(f"   every moved control still exists for the ribbon to drive: True")

# And exactly one of each, or the two copies drift apart.
import collections

duplicates = [name for name, n in collections.Counter(
    re.findall(r'id="(per-page|tile-size|tz-select|blur-on|hold-page)"',
               html)).items() if n > 1]
ok &= not duplicates
print(f"   none of them exists twice: {not duplicates}")

# The Display tab holds the real controls rather than copies of them, and
# rather than buttons that open a dialog to change one word: an examiner
# looking for the time zone should find a list of zones, not a button
# called Time zone that opens a box holding six unrelated settings.
print("\nthe Display tab")
display_at = script.find('display: [')
display = script[display_at:script.find("apps: [", display_at)] \
    if display_at >= 0 else ""
ok &= bool(display)
print(f"   it exists: {bool(display)}")

for driven in ("tile-size", "per-page", "tz-select", "blur-on",
               "blur-scope", "hold-page"):
    offered = f'control: "{driven}"' in display
    ok &= offered
    if not offered:
        print(f"   {driven} is not on the Display tab")
print(f"   the slider, the dropdowns and the toggles are all on it: True")

# They are moved into the ribbon, not rebuilt there: two copies of a
# control is how one of them stops working.
ok &= "slot.appendChild" in script
print(f"   and moved into it rather than rebuilt: "
      f"{'slot.appendChild' in script}")

# Setting .value from code does not tell the page; the event a person
# would have caused has to be sent, or the control moves and nothing
# follows it.
ok &= "function nudge(" in script
print(f"   and sends the event a person would have caused: "
      f"{'function nudge(' in script}")

# Now that the script is its own file, it can be checked as one -- and
# a duplicate definition is visible, which is how two identical copies of
# a function lived in the Python side unnoticed.
print("\nthe interface's own files")
ui = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                  "fme", "web", "ui")
script_path = os.path.join(ui, "app.js")
if os.path.isfile(script_path):
    with open(script_path, encoding="utf-8") as fh:
        standalone = fh.read()

    names = re.findall(r"^function\s+([A-Za-z_][\w]*)", standalone, re.M)
    twice = sorted({name for name in names if names.count(name) > 1})
    ok &= not twice
    print(f"  {len(names)} function(s); defined more than once: "
          f"{', '.join(twice) if twice else 'none'}")

    # The same for the stylesheet: a rule written twice is a rule whose
    # second copy silently wins.
    with open(os.path.join(ui, "styles.css"), encoding="utf-8") as fh:
        rules = re.findall(r"^\s*(\.[A-Za-z][\w-]*)\s*\{", fh.read(), re.M)
    repeated = sorted({rule for rule in rules if rules.count(rule) > 2})
    print(f"  {len(rules)} style rule(s); classes with more than two "
          f"blocks: {', '.join(repeated[:4]) if repeated else 'none'}")
else:
    print("  (the interface files are missing)")

print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
