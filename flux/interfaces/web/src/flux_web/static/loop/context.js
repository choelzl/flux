// Flux web, a loop's page (D892, D1095): the page as its parts read it -- its name and owner, its state and tab as they
// are now, what it does (goTab, refresh, drawBody) -- provided once by LoopPage, read where it is needed (`useLoop()`)
// instead of handed down through every part; a preview draws with one of its own (the layout editor's mock loop).

import { createContext, useContext } from "../view.js";

export const LoopContext = createContext(null);
export const useLoop = () => useContext(LoopContext);
