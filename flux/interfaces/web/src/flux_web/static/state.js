// Flux web's shared state (D889): what the modules read as live bindings and change through
// these setters (an imported binding cannot be assigned).
export let me = null;
export let cleanup = [];
export let pageRefresh = null;                  // a page's own redraw, when a loop changes state (D688)
export let pageOwner = null;                    // D701: whose loop the page shows (null: this user's)
export let navSeq = 0;                          // D719: each navigation's number (pageShow, in ui.js)
export let crafterCatalog = null;               // the configurator's tools, fetched once (D686)
export function setMe(v) { me = v; }
export function setPageRefresh(f) { pageRefresh = f; }
export function setPageOwner(o) { pageOwner = o; }
export function setNavSeq(n) { navSeq = n; }
export function setCrafterCatalog(c) { crafterCatalog = c; }
