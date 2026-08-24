// Coarse-grain SystemC model of evaluators/rtl's mac_array.sv (docs/calibration.md's fidelity ladder:
// a fast pre-check rung between analytic estimates and cycle-accurate RTL-sim).
//
// Loosely-timed, not cycle-by-cycle: mac_array.sv has a fully static, data-independent schedule
// (see its own docstring — the loop bounds don't depend on operand values), so its real cycle
// count is exactly
//
//     cycles = KG * B * (C + 1) + 1        where KG = K / LANES
//
// — proven by matching three real Verilator measurements across array widths 4/8/16 (see
// evaluators/systemc/README.md's derivation), not guessed. Rather than stepping the SystemC
// kernel once per cycle (which would just be a slower reimplementation of the RTL), this model
// computes the functional result directly in C++, computes that closed-form cycle count, and
// advances simulated time with a single wait() call. That's the actual point of a coarse-grain
// model: real functional correctness, a real timing number, at a small fraction of Verilator's
// compile+simulate cost — and, unlike the RTL adapter, no recompilation per shape: B/C/K/LANES
// are runtime arguments here, not compile-time Verilog parameters.
//
// For a design with genuinely data-dependent timing (arbitration, variable-latency memory,
// cache misses), this same structure still applies — compute a timing estimate however the
// design's own behaviour demands, then wait() once — but the estimate would be approximate, not
// exact, and escalating to evaluators/rtl for the real number stays necessary. Read
// i_mem.hex/w_mem.hex/expected.hex in the same format evaluators/rtl's testbench.sv uses (one
// fixed-width hex value per line — see flux_evaluator_rtl.generate_test_vectors) so both
// adapters self-check against the exact same golden reference.

#include <systemc.h>
#include <sysc/kernel/sc_spawn.h>

#include <cstdint>
#include <cstdlib>
#include <fstream>
#include <functional>
#include <iostream>
#include <string>
#include <vector>

using std::vector;

static vector<vector<int32_t>> read_hex_matrix(const std::string &path, int rows, int cols, int width_bits) {
    std::ifstream in(path);
    vector<vector<int32_t>> out(rows, vector<int32_t>(cols));
    uint32_t sign_bit = 1u << (width_bits - 1);
    uint64_t modulus = 1ull << width_bits;
    for (int r = 0; r < rows; r++) {
        for (int c = 0; c < cols; c++) {
            std::string line;
            std::getline(in, line);
            uint32_t raw = static_cast<uint32_t>(std::stoul(line, nullptr, 16));
            int32_t value = (raw & sign_bit) ? static_cast<int32_t>(raw - modulus) : static_cast<int32_t>(raw);
            out[r][c] = value;
        }
    }
    return out;
}

SC_MODULE(MacArrayCoarse) {
    int B = 0, C = 0, K = 0, LANES = 0;
    std::string work_dir;
    bool passed = false;
    long cycles = 0;

    SC_CTOR(MacArrayCoarse) {}

    void configure(int b, int c, int k, int lanes, const std::string &dir) {
        B = b;
        C = c;
        K = k;
        LANES = lanes;
        work_dir = dir;
    }

    void run() {
        auto i_mem = read_hex_matrix(work_dir + "/i_mem.hex", B, C, 8);
        auto w_mem = read_hex_matrix(work_dir + "/w_mem.hex", C, K, 8);
        auto expected = read_hex_matrix(work_dir + "/expected.hex", B, K, 16);

        // Functional computation: identical semantics to mac_array.sv's accumulation
        // (int8 x int8 summed into a wide accumulator, truncated to O_W=16 bits), computed
        // directly rather than over KG*C*B real clock cycles of gate-level signal updates.
        int errors = 0;
        for (int b = 0; b < B; b++) {
            for (int k = 0; k < K; k++) {
                int32_t acc = 0;
                for (int c = 0; c < C; c++) acc += i_mem[b][c] * w_mem[c][k];
                int32_t truncated = static_cast<int16_t>(acc & 0xFFFF);
                if (truncated != expected[b][k]) errors++;
            }
        }
        passed = (errors == 0);

        int KG = K / LANES;
        cycles = static_cast<long>(KG) * B * (C + 1) + 1;

        // One simulated 10ns "clock period" per real RTL cycle, matching testbench.sv's
        // `always #5 clk <= ~clk;` (5ns half-period). A single wait(), not one per cycle: the
        // schedule is fully static, so there is nothing for the kernel to react to mid-run.
        wait(cycles * 10, SC_NS);

        std::cout << "RESULT " << (passed ? "PASS" : "FAIL") << " cycles=" << cycles;
        if (!passed) std::cout << " errors=" << errors;
        std::cout << std::endl;
        sc_stop();
    }
};

int sc_main(int argc, char *argv[]) {
    if (argc != 6) {
        std::cerr << "usage: " << argv[0] << " B C K LANES work_dir" << std::endl;
        return 2;
    }
    int b = std::atoi(argv[1]);
    int c = std::atoi(argv[2]);
    int k = std::atoi(argv[3]);
    int lanes = std::atoi(argv[4]);
    std::string work_dir = argv[5];

    MacArrayCoarse mac("mac");
    mac.configure(b, c, k, lanes, work_dir);
    sc_spawn(std::bind(&MacArrayCoarse::run, &mac));
    sc_start();
    return mac.passed ? 0 : 1;
}
