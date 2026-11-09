"""The place list every location page shares: "Where I am" first (the browser's location, asked once a page), then
home, then the other saved places A to Z. Where the location cannot be had (refused, not found, or the page is not on
the https address) the page falls back to home and says why.

Pages put WHERE_JS into their script with `PAGE.replace("/*WHERE*/", WHERE_JS)`. It defines:

    waOrder(places)          home first, then the rest by name
    waHome(places)           the home place's id
    await waFill(sel, places, want)   fill a <select>; want (a place id or WA_ME) if given, else Where I am, else home
    await waWhere(sel)       URLSearchParams for the chosen place: place=ID, or lat and lon for Where I am
"""

WHERE_JS = """
const WA_ME = '@me';
let WA_HERE = null, WA_WHY = '', WA_ASKED = null;
function waHome(ps) { return (ps.find(p => p.kind === 'home') || ps.find(p => p.id === 'home') || ps[0] || {}).id; }
function waOrder(ps) {
  const h = waHome(ps);
  return ps.slice().sort((a, b) => (b.id === h) - (a.id === h) || String(a.name || a.id).localeCompare(String(b.name || b.id)));
}
function waLocate() {
  if (WA_ASKED) return WA_ASKED;
  WA_ASKED = new Promise(done => {
    if (!navigator.geolocation || !isSecureContext) { WA_WHY = 'your location needs the https address'; return done(null); }
    navigator.geolocation.getCurrentPosition(
      p => { WA_HERE = { lat: p.coords.latitude.toFixed(5), lon: p.coords.longitude.toFixed(5) }; WA_WHY = ''; done(WA_HERE); },
      e => { WA_WHY = e.code === 1 ? 'location not allowed' : 'location not found'; done(null); },
      { timeout: 8000, maximumAge: 120000 });
  });
  return WA_ASKED;
}
async function waFill(sel, ps, want) {
  sel.replaceChildren(new Option('Where I am', WA_ME), ...waOrder(ps).map(p => new Option(p.name || p.id, p.id)));
  sel.value = want && (want === WA_ME || ps.some(p => p.id === want)) ? want : WA_ME;
  if (sel.value === WA_ME && !(await waLocate())) sel.value = waHome(ps) || '';
  return sel.value;
}
async function waWhere(sel) {
  if (sel.value !== WA_ME) return new URLSearchParams({ place: sel.value });
  if (await waLocate()) return new URLSearchParams(WA_HERE);
  const m = document.getElementById('msg');
  sel.value = sel.options.length > 1 ? sel.options[1].value : '';
  if (m) m.textContent = 'Showing home: ' + WA_WHY + '.';
  return new URLSearchParams({ place: sel.value });
}
"""
