// Hand-written, deliberately minimal 8-wide MAC array — the same (B, C, K, LANES) shape as
// ir/architecture/examples/simple-npu-1d-v1.yaml (a single spatial dim, X=8) and
// ir/workload/examples/mlp-gemm0.yaml (B=4, C=32, K=32), so its real, Verilator-measured cycle
// count is directly comparable to evaluators/zigzag's (1554) and evaluators/timeloop's (512)
// results for the identical (workload, architecture) shape — a third, real ground-truth data
// point for docs/phase1-exit-criterion-report.md's latency investigation, not another analytic
// estimate.
//
// Loop structure (fixed in hardware, not Mapping-IR-controlled — this is evaluators/rtl's v0.1,
// proving the adapter's ABI integration on a small hand-written design, not implementing a
// configurable accelerator): temporal loops over kg (K-group, 0..K/LANES-1), c (reduction,
// 0..C-1), b (batch, 0..B-1), each cycle issuing LANES=8 parallel MACs — one whole K-group's
// worth of output lanes accumulate every cycle. Every operand is preloaded into on-chip memory
// once (via $readmemh in the testbench) before `start`, matching Timeloop's own winning
// mapping's "every operand loaded from DRAM exactly once" structure
// (docs/phase1-exit-criterion-report.md point 5) — this RTL has no explicit DRAM/gbuf split at
// all, deliberately: modelling a real memory hierarchy's timing is out of scope for proving the
// adapter works.
//
// Precision: internal accumulators are 32-bit (generous headroom for int8*int8 sums over
// C=32 — max magnitude 32*127*127 < 2^19), output truncated to 16 bits per
// ir/workload/examples/mlp-gemm0.yaml's declared `O: 16` precision. The testbench restricts test
// data magnitude so real sums never overflow 16 bits (documented there, not enforced here).
module mac_array #(
    parameter int B      = 4,
    parameter int C      = 32,
    parameter int K      = 32,
    parameter int LANES  = 8,
    parameter int DATA_W = 8,
    parameter int ACC_W  = 32,
    parameter int O_W    = 16
) (
    input  logic clk,
    input  logic rst_n,
    input  logic start,
    output logic done
);

    localparam int KG = K / LANES;

    // Operand/result memories — populated and read by the testbench via hierarchical reference
    // (dut.i_mem, dut.w_mem, dut.o_mem), not through explicit ports: this is a simulation-only
    // v0.1 harness, not a synthesizable memory-mapped interface.
    logic signed [DATA_W-1:0] i_mem[0:B-1][0:C-1];
    logic signed [DATA_W-1:0] w_mem[0:C-1][0:K-1];
    logic signed [O_W-1:0]    o_mem[0:B-1][0:K-1];

    logic signed [ACC_W-1:0] acc[0:B-1][0:KG-1][0:LANES-1];

    typedef enum logic [1:0] {
        IDLE,
        RUN,
        DRAIN,
        DONE
    } state_t;
    state_t state;

    int unsigned b, c, kg;
    int unsigned drain_b, drain_kg;

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state <= IDLE;
            done <= 1'b0;
            b <= 0;
            c <= 0;
            kg <= 0;
            drain_b <= 0;
            drain_kg <= 0;
        end else begin
            case (state)
                IDLE: begin
                    done <= 1'b0;
                    if (start) begin
                        state <= RUN;
                        b <= 0;
                        c <= 0;
                        kg <= 0;
                        for (int bi = 0; bi < B; bi++)
                            for (int kgi = 0; kgi < KG; kgi++)
                                for (int li = 0; li < LANES; li++) acc[bi][kgi][li] <= '0;
                    end
                end

                RUN: begin
                    for (int li = 0; li < LANES; li++) begin
                        acc[b][kg][li] <= acc[b][kg][li] +
                            $signed(i_mem[b][c]) * $signed(w_mem[c][kg*LANES+li]);
                    end

                    if (b == B - 1) begin
                        b <= 0;
                        if (c == C - 1) begin
                            c <= 0;
                            if (kg == KG - 1) begin
                                state <= DRAIN;
                                drain_b <= 0;
                                drain_kg <= 0;
                            end else begin
                                kg <= kg + 1;
                            end
                        end else begin
                            c <= c + 1;
                        end
                    end else begin
                        b <= b + 1;
                    end
                end

                DRAIN: begin
                    for (int li = 0; li < LANES; li++) begin
                        o_mem[drain_b][drain_kg*LANES+li] <= acc[drain_b][drain_kg][li][O_W-1:0];
                    end
                    if (drain_b == B - 1) begin
                        drain_b <= 0;
                        if (drain_kg == KG - 1) begin
                            state <= DONE;
                        end else begin
                            drain_kg <= drain_kg + 1;
                        end
                    end else begin
                        drain_b <= drain_b + 1;
                    end
                end

                DONE: begin
                    done <= 1'b1;
                end
            endcase
        end
    end

endmodule
