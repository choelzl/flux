# The L2 prefetcher configuration: what an `.ini` may say

The file is ChampSim knobs, one `name = value` per line. `bingo_default.ini` beside this file is
the shipped configuration (Bingo alone); start from it. Only the knobs on this page: any other
(a simulator knob such as `simulation_instructions` or `dram_io_freq`) is refused.

## The stack

`l2c_prefetcher_types = bingo[,partner,...]`: Bingo first, plus any partners beside it in the L2
slot, each once: `sms`, `ampm`, `stride`, `streamer`, `spp_ppf_dev`, `power7`, `sandbox`,
`spp_dev2`, `ipcp`. `scooby`, `mlop` and `next_line` crash beside Bingo and are refused. A
partner's knob is allowed only when the partner is in the stack; a knob left out takes its
shipped value.

## Bingo (shipped value in brackets)

- `bingo_region_size` [2048]: bytes learned per region, a power of two, 64..4096.
  `bingo_pattern_len` must equal region_size / 64 (bingo.cc aborts otherwise).
- `bingo_ft_size` [64], `bingo_at_size` [128]: regions observed and accumulated at once (16-way).
- `bingo_pht_size` [4096], `bingo_pht_ways` [16]: the learned-pattern table; size = ways x sets,
  sets a power of two. Usually where both the speedup and the storage are.
- `bingo_pf_streamer_size` [128]: outstanding prefetches (16-way).
- `bingo_pc_width` [16], `bingo_min_addr_width` [5], `bingo_max_addr_width` [16]: the PC and
  address bits that key the pattern table, 0..30; max >= min; pc + min > 0.
- `bingo_l2c_thresh` [0.8]: confidence before the L2 prefetches, 0..1, lower is more aggressive;
  it costs no storage.
- Keep `bingo_debug_level = 0`, `bingo_l1d_thresh = 1.01`, `bingo_llc_thresh = 0.05`,
  `bingo_pc_address_fill_level = L2`; another value is refused.

Every table keeps a tag: key bits - log2(sets) >= 0, where the filter/accumulation key is
48 - log2(region_size), the streamer's 64 - log2(region_size) and the pattern table's
pc_width + max_addr_width.

## Storage (`storage_bytes`)

Each Bingo table costs entries x (tag + payload + valid + LRU) bits. The pattern table is 31 KB
of the shipped 35 KB. The accumulation payload grows with pattern_len, the streamer's is
2 x pattern_len, the pattern table's is pattern_len.

Each partner adds its tables (`bingo.py`, from the fields its Pythia source keeps: 48-bit
physical addresses, the PC whole at 48 bits, a fully associative table keeps its full key, a
valid bit and an LRU position). At the shipped values: sms 32,456 B (its pattern table
2048 x (47-bit tag + 64-bit pattern) is most of it), spp_ppf_dev 57,217 B, spp_dev2 5,508 B,
power7 4,736 B, stride 4,192 B, ipcp 2,048 B, ampm 856 B, streamer 416 B, sandbox 301 B. A
size knob (`sms_pht_size`, `stride_num_trackers`, `sandbox_bloom_filter_size`, ...) scales its
table; a degree or threshold costs nothing.

## Partners' knobs (shipped value in brackets)

Degrees are 0..64, sizes and counts at least 1.

- sms: `sms_pref_degree` [4], `sms_pht_size` [2048], `sms_pht_assoc` [16], `sms_region_size`
  [4096], `sms_ft_size` [64], `sms_at_size` [32], `sms_pref_buffer_size` [256]. The region is a
  power of two, 64..4096; the pattern table's sets (size / assoc) a power of two.
- ampm: `ampm_pref_degree` [4], `ampm_pred_degree` [4], `ampm_pb_size` [64],
  `ampm_pref_buffer_size` [256] (the buffer is off, so it changes nothing)
- stride: `stride_pref_degree` [2], `stride_num_trackers` [256]
- streamer: `streamer_pref_degree` [5], `streamer_num_trackers` [64]
- spp_ppf_dev: `ppf_perc_threshold_hi` [-5], `ppf_perc_threshold_lo` [-15], -256..256, lo <= hi
- power7: `power7_default_streamer_degree` [4], `power7_explore_epoch` [20000],
  `power7_exploit_epoch` [200000]. It runs a stride and a streamer of its own, sized by the
  stride and streamer knobs above.
- sandbox: `sandbox_pref_degree` [4], `sandbox_num_access_in_phase` [256],
  `sandbox_bloom_filter_size` [2048] (at least num_access_in_phase), `sandbox_num_cycle_offsets`
  [4] (0..16)
- spp_dev2, ipcp: no knobs
