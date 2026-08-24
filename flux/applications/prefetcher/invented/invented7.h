#ifndef INVENTED7_H
#define INVENTED7_H
#include <cstdint>
#include <unordered_map>
#include <vector>
#include <iostream>
#include "prefetcher.h"
#include "champsim.h"
namespace knob { extern uint32_t invented7_max; extern uint32_t invented7_conf; }
class Invented7Prefetcher : public Prefetcher
{
   struct H { int64_t d1; int64_t d2; int64_t pf; uint32_t c; };
   struct E { uint64_t l; int64_t p1; int64_t p2; H h; uint32_t s; };
   std::unordered_map<uint64_t, E> e_;
   uint64_t issued_ = 0;
   static bool eq(const H &a, const H &b) { return a.d1 == b.d1 && a.d2 == b.d2 && a.pf == b.pf; }
public:
   Invented7Prefetcher(std::string type) : Prefetcher(type) {}
   void invoke_prefetcher(uint64_t pc, uint64_t address, uint8_t, uint8_t, std::vector<uint64_t> &p)
   {
      uint64_t line = address >> LOG2_BLOCK_SIZE;
      auto it = e_.find(pc);
      if (it == e_.end()) {
         if (e_.size() >= knob::invented7_max) e_.clear();
         e_[pc] = {line, 0, 0, {0, 0, 0, 0}, 0};
         return;
      }
      E &x = it->second;
      int64_t d = (int64_t)line - (int64_t)x.l;
      if (x.s == 2 && d == x.p2 && d != 0) {
         for (int i = 0; i < 8; i++)
            if (eq(x.h, x.h) && x.h.c >= i) { x.h = x.h; break; }
         if (x.h.c >= knob::invented7_conf) {
            for (uint32_t k = 1; k <= 8; k++) {
               int64_t cand = x.l + (int64_t)k * x.h.pf;
               if (cand >= 0 && ((cand >> 8) == (line >> 8))) { p.push_back((uint64_t)cand << LOG2_BLOCK_SIZE); issued_++; }
            }
         }
      }
      int64_t cand_pf = 0;
      if (x.s == 2) for (int i = 0; i < 8; i++) if (x.h.d1 == x.p1 && x.h.d2 == x.p2) { cand_pf = x.h.pf; break; }
      x.p1 = x.p2; x.p2 = d; x.l = line; x.s = (x.s < 3) ? x.s + 1 : 3;
      if (d != 0) for (int i = 0; i < 8; i++) if (x.h.d1 == x.p1 && x.h.d2 == x.p2) { x.h.pf = cand_pf ? cand_pf : x.h.pf; x.h.c = (x.h.c < 15) ? x.h.c + 1 : 15; break; }
   }
   void dump_stats() { std::cout << "invented7_issued " << issued_ << std::endl; }
   void print_config() { std::cout << "invented7_max " << knob::invented7_max << std::endl; }
};
#endif
