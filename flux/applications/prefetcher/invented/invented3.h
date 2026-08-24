#ifndef INVENTED3_H
#define INVENTED3_H
#include <cstdint>
#include <list>
#include <unordered_map>
#include <vector>
#include <iostream>
#include "prefetcher.h"
#include "champsim.h"

namespace knob { extern uint32_t invented3_degree; extern uint32_t invented3_max_lines; }

class Invented3Prefetcher : public Prefetcher
{
  uint64_t tag(uint64_t line) { return line >> 6; }
  uint64_t page(uint64_t line) { return line >> 10; }
  std::list<uint64_t> L;
  std::unordered_map<uint64_t, std::list<uint64_t>::iterator> M;
  uint64_t issued_ = 0;
public:
  Invented3Prefetcher(std::string type) : Prefetcher(type) {}
  void invoke_prefetcher(uint64_t pc, uint64_t address, uint8_t, uint8_t, std::vector<uint64_t> &pref_addr)
  {
    uint64_t line = address >> LOG2_BLOCK_SIZE;
    uint64_t t = tag(line);
    auto it = M.find(t);
    if (it != M.end()) {
      L.erase(it->second);
      M.erase(it);
    }
    L.push_front(line);
    M[t] = L.begin();
    while (M.size() > knob::invented3_max_lines) {
      auto ev = std::prev(L.end());
      M.erase(tag(*ev));
      L.pop_back();
    }
    uint64_t p = page(line);
    for (uint32_t k = 1; k <= knob::invented3_degree; k++) {
      uint64_t nl = line + k;
      if (page(nl) != p) break;
      pref_addr.push_back(nl << LOG2_BLOCK_SIZE);
      issued_++;
    }
  }
  void dump_stats() { std::cout << "invented3_issued " << issued_ << std::endl; }
  void print_config() { std::cout << "invented3_degree " << knob::invented3_degree << std::endl; }
};
#endif
