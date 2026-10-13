// Ping-pong in adevs, following the DEVS graph from the capstone meeting:
// each player is Send (ta=0.1) / Wait (ta=inf), with !send (output port)
// and ?receive (input port). A starts in Send, B starts in Wait.
//
// Adapted from adevs examples/tutorial/ex2.cpp (Supplier/Consumer):
// same Atomic/Graph/Simulator structure, proper input+output pins.

#include <iostream>
#include <string>
#include "adevs/adevs.h"

using Atomic = adevs::Atomic<>;
using PinValue = adevs::PinValue<>;
using Simulator = adevs::Simulator<>;
using Graph = adevs::Graph<>;
using pin_t = adevs::pin_t;

class Player : public Atomic {
  public:
    pin_t send;     // output port (!send)
    pin_t receive;  // input port (?receive)

    Player(std::string name, bool starts_with_ball)
        : Atomic(), name(name) {
        phase = starts_with_ball ? SEND : WAIT;
    }

    double ta() override {
        return phase == SEND ? 0.1 : adevs_inf<double>();
    }

    void output_func(std::list<PinValue> &yb) override {
        if (phase == SEND) {
            std::cout << "  " << name << " !send ball" << std::endl;
            yb.push_back(PinValue(send, std::string("ball")));
        }
    }

    void delta_int() override {
        phase = WAIT;  // hit it: Send -> Wait
    }

    void delta_ext(double, std::list<PinValue> const &xb) override {
        for (auto const &input : xb) {
            if (input.pin == receive) {
                phase = SEND;  // ?receive: caught it, Wait -> Send
            }
        }
    }

    void delta_conf(std::list<PinValue> const &xb) override {
        delta_ext(ta(), xb);
        delta_int();
    }

    std::string phase_name() const {
        return phase == SEND ? "Send" : "Wait";
    }

  private:
    enum Phase { SEND, WAIT };
    Phase phase;
    std::string name;
};

int main() {
    auto A = std::make_shared<Player>("A", true);   // A starts in Send
    auto B = std::make_shared<Player>("B", false);  // B starts in Wait

    auto graph = std::make_shared<Graph>();
    graph->add_atomic(A);
    graph->add_atomic(B);
    graph->connect(A->send, B->receive);  // A !send -> B ?receive
    graph->connect(B->send, A->receive);  // B !send -> A ?receive
    // Register each model on its own input pin so pin-to-pin
    // routing can deliver to it.
    graph->connect(A->receive, A);
    graph->connect(B->receive, B);

    Simulator sim(graph);
    double end = 1.0;
    while (sim.nextEventTime() <= end) {
        std::cout << "t=" << sim.nextEventTime() << std::endl;
        sim.execNextEvent();
        std::cout << "  A:" << A->phase_name()
                  << " B:" << B->phase_name() << std::endl;
    }
    return 0;
}
