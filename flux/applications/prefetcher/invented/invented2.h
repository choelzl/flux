#ifndef INVENTED2_H
#define INVENTED2_H
#include <cstdint>
#include <unordered_map>
#include <vector>
#include <iostream>
#include "prefetcher.h"
#include "champsim.h"

namespace knob { extern uint32_t invented2_conf; extern uint32_t invented2_max_pcs; }

class Invented2Prefetcher : public Prefetcher
{
   struct P { int64_t last; int64_t pd; int64_t best; uint32_t conf; };
   std::unordered_map<uint64_t, P> m_;
public:
   Invented2Prefetcher(std::string type) : Prefetcher(type) {}
   void invoke_prefetcher(uint64_t pc, uint64_t address, uint8_t, uint8_t,
                          std::vector<uint64_t> &out)
   {
      uint64_t line = address >> LOG2_BLOCK_SIZE;
      auto it = m_.find(pc);
      if (it == m_.end()) {
         if (m_.size() >= knob::invented2_max_pcs) m_.clear();
         m_[pc] = {line, 0, 0, 0};
         return;
      }
      int64_t d = (int64_t)line - (int64_t)it->second.last;
      if (d == it->second.pd && d != 0) {
         if (it->second.best != d) { it->second.best = d; it->second.conf = 1; }
         else if (it->second.conf < 255) it->second.conf++;
         if (it->second.conf >= knob::invented2_conf)
            out.push_back(((int64_t)line + it->second.best) << LOG2_BLOCK_SIZE);
      } else { it->second.best = d; it->second.conf = 0; }
      it->second.pd = d;
      it->second.last = line;
   }
   void dump_stats() { std::cout << "invented2 tracked " << m_.size() << std::endl; }
   void print_config() { std::cout << "invented2_conf " << knob::invented2_conf << std::endl; }
};
#endif
