// A pretend browser: enough for the page's script to run to the end, so a
// crash at startup is caught here rather than by an examiner looking at a
// page where nothing works.
const made = new Map();
function element(id) {
  if (made.has(id)) return made.get(id);
  const el = {
    id, value: "", textContent: "", innerHTML: "", title: "", src: "",
    checked: false, disabled: false, dataset: {},
    style: new Proxy({setProperty(){}, removeProperty(){}, getPropertyValue(){return "";}},
                     {get: (t, k) => (k in t ? t[k] : ""), set: () => true}),
    classList: {add(){}, remove(){}, toggle(){}, contains(){return false;}},
    addEventListener(){}, removeEventListener(){}, appendChild(){}, remove(){},
    insertBefore(){}, setAttribute(){}, getAttribute(){return null;},
    removeAttribute(){},
    // A real element's querySelector finds its children; returning null
    // here makes the shim, not the program, the thing that fails.
    querySelector(){ return element("child-of-" + id); },
    querySelectorAll(){return [];},
    getBoundingClientRect(){return {left:0,top:0,right:0,bottom:0,width:100,height:100};},
    scrollIntoView(){}, scrollTo(){}, focus(){}, click(){}, load(){}, pause(){},
    closest(){return null;}, contains(){return false;},
    // Enough for the ribbon's own controls to be exercised: changing a
    // slider or a dropdown from code means dispatching the event the page
    // listens for, and a <select> has options to walk.
    dispatchEvent(){ return true; },
    options: [{value: "graded", textContent: "the categories chosen below"},
              {value: "all", textContent: "every image"},
              {value: "ungraded", textContent: "anything not yet graded"}],
    min: "90", max: "420",
  };
  made.set(id, el);
  return el;
}
global.document = {
  getElementById: element,
  createElement: () => element("made-" + Math.random()),
  querySelector: (sel) => element("sel" + sel),
  querySelectorAll: () => [],
  addEventListener(){}, removeEventListener(){},
  body: element("body"), documentElement: element("html"),
};
global.window = {addEventListener(){}, open(){}, location:{href:"", hash:""},
                 matchMedia: () => ({matches:false, addEventListener(){}}),
                 devicePixelRatio: 1};
global.navigator = {userAgent: "shim", clipboard: {writeText: async () => {}}};
global.fetch = async () => ({ok: true, status: 200, json: async () => ({ok: true}),
                             text: async () => "", headers: {get: () => null}});
global.requestAnimationFrame = (fn) => { return 0; };
global.cancelAnimationFrame = () => {};
global.setTimeout = (fn, ms) => 0;
global.setInterval = () => 0;
global.clearTimeout = () => {};
global.IntersectionObserver = class { observe(){} unobserve(){} disconnect(){} };
global.CSS = {escape: (s) => String(s)};
global.alert = () => {};
global.confirm = () => false;
global.Intl = Intl;
