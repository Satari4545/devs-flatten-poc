// Simulation driver: runs the GenModel that gen/top.hpp aliases to the
// generator-produced top model (that dir goes on the -I path).
//
//   sim_driver --time T [--out PATH] [--max-events N]
//
// Writes a CSV trace: time,model_id,model_name,port_name,data
// (state rows have an empty port_name). Times and real values are written
// with 17 significant digits, so no two different doubles look the same.
// With --max-events, a trace that would grow past N rows stops the run with
// exit code 3 (e.g. a zero time advance that never reaches the end time).

#include <cadmium/core/logger/logger.hpp>
#include <cadmium/core/modeling/atomic.hpp>
#include <cadmium/core/modeling/coupled.hpp>
#include <cadmium/core/simulation/root_coordinator.hpp>
#include <exception>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <memory>
#include <sstream>
#include <stdexcept>
#include <string>

// Real-valued messages: full precision instead of Cadmium's 6 digits.
// Declared before the generated model instantiates Port<double>.
template <>
std::string cadmium::_Port<double>::logMessage(std::size_t i) const {
    std::ostringstream ss;
    ss << std::setprecision(17) << bag.at(i);
    return ss.str();
}

#include "gen/top.hpp"

const int kTooManyEvents = 3;  // exit code when --max-events is exceeded

struct TooManyEvents {
    double time;
};

// Same columns as cadmium::CSVLogger, with full-precision times.
class TraceLogger : public cadmium::Logger {
    std::ofstream file;
    long maxRows;  // 0: no limit
    long rows = 0;

    void startRow(double time) {
        if (maxRows && ++rows > maxRows) throw TooManyEvents{time};
        file << time << ',';
    }

 public:
    TraceLogger(const std::string& path, long maxRows) : file(path), maxRows(maxRows) {
        file << std::setprecision(17);
    }
    void start() override { file << "time,model_id,model_name,port_name,data\n"; }
    void stop() override { file.close(); }
    void logOutput(double time, long id, const std::string& model,
                   const std::string& port, const std::string& data) override {
        startRow(time);
        file << id << ',' << model << ',' << port << ',' << data << '\n';
    }
    void logState(double time, long id, const std::string& model,
                  const std::string& state) override {
        startRow(time);
        file << id << ',' << model << ",," << state << '\n';
    }
};

int main(int argc, char* argv[]) {
    const char* usage = "usage: sim_driver --time T [--out PATH] [--max-events N]\n";
    std::string endTime;
    std::string outPath = "trace.csv";
    long maxEvents = 0;

    try {
        for (int i = 1; i < argc; ++i) {
            std::string arg = argv[i];
            if (arg == "--time" && i + 1 < argc) {
                endTime = argv[++i];
            } else if (arg == "--out" && i + 1 < argc) {
                outPath = argv[++i];
            } else if (arg == "--max-events" && i + 1 < argc) {
                maxEvents = std::stol(argv[++i]);
            } else {
                throw std::invalid_argument(arg);
            }
        }
        if (endTime.empty()) throw std::invalid_argument("--time");
        std::stod(endTime);
    } catch (const std::exception&) {
        std::cerr << usage;
        return 1;
    }

    try {
        auto model = std::make_shared<GenModel>("top");
        auto coordinator = cadmium::RootCoordinator(model);
        coordinator.setLogger(std::make_shared<TraceLogger>(outPath, maxEvents));
        coordinator.start();
        coordinator.simulate(std::stod(endTime));
        coordinator.stop();
    } catch (const TooManyEvents& e) {
        std::cerr << "more than " << maxEvents << " trace rows before t=" << e.time << '\n';
        return kTooManyEvents;
    } catch (const std::exception& e) {
        std::cerr << "simulation error: " << e.what() << '\n';
        return 1;
    }
    return 0;
}
