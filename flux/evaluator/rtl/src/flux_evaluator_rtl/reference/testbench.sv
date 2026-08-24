// Self-checking testbench for mac_array.sv. Reads three $readmemh hex files the Python adapter
// generates (i_mem.hex, w_mem.hex, expected.hex — two's-complement bytes/halfwords, one per
// line, row-major flattened to match the DUT's unpacked-array declaration order), drives the
// DUT, counts real clock cycles from `start` assertion to `done` assertion, compares every
// output element, and prints exactly one machine-parseable result line:
//   RESULT PASS cycles=<N>
//   RESULT FAIL cycles=<N> errors=<M>
// adapter.py greps for this line rather than parsing full VCD/log output — the same
// "parse the tool's own stable summary line" discipline evaluators/timeloop's adapter.py uses
// for Timeloop's `Summary Stats` block.
//
// B/C/K/LANES are real module `parameter`s (not `localparam`), overridable at the command line
// via Verilator's `-GB=... -GC=... -GK=... -GLANES=...` — adapter.py uses this to drive the
// same fixed RTL schedule across different (workload, architecture) shapes without editing or
// regenerating this file per run.
`timescale 1ns / 1ps

module testbench #(
    parameter int B      = 4,
    parameter int C      = 32,
    parameter int K      = 32,
    parameter int LANES  = 8,
    parameter int DATA_W = 8,
    parameter int O_W    = 16
) ();

    logic clk;
    logic rst_n = 0;
    logic start = 0;
    logic done;

    mac_array #(
        .B(B), .C(C), .K(K), .LANES(LANES), .DATA_W(DATA_W), .O_W(O_W)
    ) dut (
        .clk(clk), .rst_n(rst_n), .start(start), .done(done)
    );

    initial clk = 0;
    always #5 clk <= ~clk;

    integer cycle_count;
    integer errors;
    logic signed [O_W-1:0] expected[0:B-1][0:K-1];

    initial begin
        $readmemh("i_mem.hex", dut.i_mem);
        $readmemh("w_mem.hex", dut.w_mem);
        $readmemh("expected.hex", expected);

        rst_n = 0;
        start = 0;
        cycle_count = 0;
        repeat (2) @(posedge clk);
        rst_n = 1;
        @(posedge clk);
        start = 1;
        @(posedge clk);
        start = 0;

        while (!done) begin
            @(posedge clk);
            cycle_count = cycle_count + 1;
        end

        errors = 0;
        for (int b = 0; b < B; b++) begin
            for (int k = 0; k < K; k++) begin
                if (dut.o_mem[b][k] !== expected[b][k]) begin
                    errors = errors + 1;
                    $display("MISMATCH b=%0d k=%0d got=%0d expected=%0d", b, k, dut.o_mem[b][k],
                              expected[b][k]);
                end
            end
        end

        if (errors == 0) $display("RESULT PASS cycles=%0d", cycle_count);
        else $display("RESULT FAIL cycles=%0d errors=%0d", cycle_count, errors);

        $finish;
    end
endmodule
