﻿using System;
using System.Collections.Generic;
using System.Linq;
using System.IO;
using System.Threading.Tasks;
using System.Reactive.Subjects;
using Microsoft.AspNetCore.Mvc;
using Newtonsoft.Json;
using Validation;

using AirTrafficControl.Interfaces;
using System.Text;

namespace atcsvc.Controllers
{
    [Route("api/[controller]")]
    public class FlightsController : Controller
    {
        private readonly ISubject<Airplane> airplaneStateEventAggregator_;
        private readonly AtcSvc atcSvc_;

        public FlightsController(ISubject<Airplane> airplaneStateEventAggregator, AtcSvc atcSvc): base()
        {
            Requires.NotNull(airplaneStateEventAggregator, nameof(airplaneStateEventAggregator));
            Requires.NotNull(atcSvc, nameof(atcSvc));

            airplaneStateEventAggregator_ = airplaneStateEventAggregator;
            atcSvc_ = atcSvc;
        }

        // GET api/flights
        [HttpGet]
        public IActionResult GetAllFlights()
        {
            // This does not fully comply with the server-sent events spec 
            // https://html.spec.whatwg.org/multipage/server-sent-events.html#server-sent-events  https://www.html5rocks.com/en/tutorials/eventsource/basics/
            // but is good enough for testing
            return new PushStreamResult("text/event-stream", (stream, cToken) => {
                airplaneStateEventAggregator_.Subscribe(new AirplaneStatePublisher(stream), cToken);
                return cToken.WhenCanceled();
            });
        }

        // PUT api/flights
        [HttpPut]
        public async Task<IActionResult> StartNewFlightAsync([FromBody] FlightPlan flightPlan)
        {
            await atcSvc_.StartNewFlight(flightPlan);
            return NoContent();
        }
    }
}
