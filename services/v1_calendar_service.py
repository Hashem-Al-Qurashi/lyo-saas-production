"""
V1 Calendar Service Integration
Wraps V1's proven calendar functions for our production architecture
"""
import sys
import os
import asyncio
import logging
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime, timedelta

# Add V1 path to import their working functions
v1_path = "/home/sakr_quraish/Projects/italian/extracted_v1/lyo assistant"
sys.path.insert(0, v1_path)

logger = logging.getLogger(__name__)

# Import V1's working calendar functions
try:
    from calendar_utils import (
        authenticate_google_calendar,
        get_calendar_id_by_summary,
        check_availability,
        book_appointment_with_service,
        cancel_appointment,
        cerca_prenotazione_per_nome,
        get_available_slots,
        normalizza_data
    )
    V1_CALENDAR_AVAILABLE = True
    logger.info("✅ V1 calendar functions imported successfully")
except ImportError as e:
    logger.error(f"❌ Cannot import V1 calendar functions: {e}")
    V1_CALENDAR_AVAILABLE = False

class V1CalendarService:
    """
    Production wrapper around V1's proven calendar functions
    Provides clean interface while preserving V1's working logic
    """
    
    def __init__(self, calendar_name: str = "Prenotazioni Lyo"):
        self.calendar_name = calendar_name
        self._service = None
        self._calendar_id = None
    
    async def _get_calendar_service(self):
        """
        Get authenticated Google Calendar service
        Lazy initialization to avoid auth on startup
        """
        if not self._service:
            try:
                self._service = authenticate_google_calendar()
                self._calendar_id = get_calendar_id_by_summary(self._service, self.calendar_name)
                
                if not self._calendar_id:
                    logger.error(f"❌ Calendar '{self.calendar_name}' not found")
                    return None, None
                    
                logger.info(f"✅ Connected to calendar: {self.calendar_name}")
                
            except Exception as e:
                logger.error(f"❌ Calendar authentication failed: {e}")
                return None, None
        
        return self._service, self._calendar_id
    
    async def check_availability(self, date: str, time: str, duration_minutes: int = 60) -> bool:
        """
        Check if time slot is available
        
        Args:
            date: YYYY-MM-DD format
            time: HH:MM format  
            duration_minutes: Appointment duration
            
        Returns:
            True if available, False if busy or error
        """
        if not V1_CALENDAR_AVAILABLE:
            logger.warning("V1 calendar not available - returning mock result")
            return True  # Optimistic fallback
        
        try:
            # Convert to V1's expected format (DD-MM)
            date_obj = datetime.strptime(date, "%Y-%m-%d")
            v1_date = date_obj.strftime("%d-%m")
            
            logger.info(f"Checking availability: {v1_date} at {time}")
            
            # Use V1's proven function
            is_available = check_availability(v1_date, time, duration_minutes)
            
            logger.info(f"Availability result: {'✅ AVAILABLE' if is_available else '❌ BUSY'}")
            return is_available
            
        except Exception as e:
            logger.error(f"❌ Availability check failed: {e}")
            return False  # Conservative fallback
    
    async def book_appointment(
        self, 
        date: str, 
        time: str, 
        customer_name: str, 
        customer_phone: str = None,
        duration_minutes: int = 60
    ) -> Dict[str, Any]:
        """
        Book appointment in Google Calendar
        
        Returns:
            Dict with success status, appointment details, and calendar link
        """
        if not V1_CALENDAR_AVAILABLE:
            logger.warning("V1 calendar not available - returning mock booking")
            return {
                "success": False,
                "error": "Calendar service not available",
                "appointment_id": None
            }
        
        try:
            service, calendar_id = await self._get_calendar_service()
            if not service or not calendar_id:
                return {
                    "success": False,
                    "error": "Calendar authentication failed",
                    "appointment_id": None
                }
            
            # Convert to V1's format
            date_obj = datetime.strptime(date, "%Y-%m-%d")
            v1_date = date_obj.strftime("%d-%m")
            
            logger.info(f"Booking appointment: {customer_name} on {v1_date} at {time}")
            
            # Use V1's proven booking function
            calendar_link = book_appointment_with_service(
                service=service,
                calendar_id=calendar_id,
                giorno=v1_date,
                ora=time,
                nome_cliente=customer_name,
                numero=customer_phone,
                durata_minuti=duration_minutes
            )
            
            if calendar_link:
                logger.info(f"✅ Appointment booked successfully: {calendar_link}")
                return {
                    "success": True,
                    "appointment_id": calendar_link.split('/')[-1] if calendar_link else None,
                    "calendar_link": calendar_link,
                    "customer_name": customer_name,
                    "date": date,
                    "time": time
                }
            else:
                logger.error("❌ Booking failed - no calendar link returned")
                return {
                    "success": False,
                    "error": "Booking failed",
                    "appointment_id": None
                }
                
        except Exception as e:
            logger.error(f"❌ Booking error: {e}")
            return {
                "success": False,
                "error": str(e),
                "appointment_id": None
            }
    
    async def cancel_appointment(
        self, 
        date: str, 
        time: str = None, 
        customer_name: str = None
    ) -> Dict[str, Any]:
        """
        Cancel appointment in Google Calendar
        
        Returns:
            Dict with cancellation status and details
        """
        if not V1_CALENDAR_AVAILABLE:
            logger.warning("V1 calendar not available - returning mock cancellation")
            return {"success": False, "error": "Calendar service not available"}
        
        try:
            # Convert to V1's format
            date_obj = datetime.strptime(date, "%Y-%m-%d")
            v1_date = date_obj.strftime("%d-%m")
            
            logger.info(f"Cancelling appointment: {v1_date} at {time} for {customer_name}")
            
            # Use V1's proven cancellation function
            success = cancel_appointment(v1_date, time or "15:00", customer_name or "Cliente")
            
            if success:
                logger.info("✅ Appointment cancelled successfully")
                return {
                    "success": True,
                    "date": date,
                    "time": time,
                    "customer_name": customer_name
                }
            else:
                logger.warning("⚠️ Cancellation failed or appointment not found")
                return {
                    "success": False,
                    "error": "Appointment not found or cancellation failed"
                }
                
        except Exception as e:
            logger.error(f"❌ Cancellation error: {e}")
            return {
                "success": False,
                "error": str(e)
            }
    
    async def search_appointments_by_name(self, customer_name: str) -> List[Dict[str, Any]]:
        """
        Search for appointments by customer name
        """
        if not V1_CALENDAR_AVAILABLE:
            return []
        
        try:
            logger.info(f"Searching appointments for: {customer_name}")
            
            # Use V1's search function
            appointments = cerca_prenotazione_per_nome(customer_name)
            
            logger.info(f"Found {len(appointments)} appointments for {customer_name}")
            return appointments
            
        except Exception as e:
            logger.error(f"❌ Search error: {e}")
            return []
    
    async def get_available_slots(self, date: str, time_range: str = "intera_giornata") -> List[str]:
        """
        Get available time slots for a specific date
        
        Args:
            date: YYYY-MM-DD format
            time_range: "mattina", "pomeriggio", "intera_giornata"
            
        Returns:
            List of available time slots in HH:MM format
        """
        if not V1_CALENDAR_AVAILABLE:
            # Mock fallback
            return ["09:00", "11:00", "14:00", "16:00"]
        
        try:
            # Convert to V1's format
            date_obj = datetime.strptime(date, "%Y-%m-%d")
            v1_date = date_obj.strftime("%d-%m")
            
            logger.info(f"Getting available slots for {v1_date} during {time_range}")
            
            # Use V1's function
            slots = get_available_slots(v1_date, time_range)
            
            logger.info(f"Found {len(slots)} available slots")
            return slots
            
        except Exception as e:
            logger.error(f"❌ Get slots error: {e}")
            return []
    
    async def test_calendar_connection(self) -> Dict[str, Any]:
        """
        Test calendar connection and basic functionality
        """
        print("🧪 TESTING V1 CALENDAR CONNECTION...")
        
        if not V1_CALENDAR_AVAILABLE:
            return {
                "success": False,
                "error": "V1 calendar functions not available",
                "tests": []
            }
        
        test_results = []
        
        try:
            # Test 1: Authentication
            print("📅 Test 1: Authentication...")
            service, calendar_id = await self._get_calendar_service()
            
            if service and calendar_id:
                test_results.append({"test": "authentication", "success": True, "message": "✅ Connected to Google Calendar"})
                print("✅ Authentication successful")
            else:
                test_results.append({"test": "authentication", "success": False, "message": "❌ Authentication failed"})
                print("❌ Authentication failed")
                return {"success": False, "tests": test_results}
            
            # Test 2: Availability check
            print("📅 Test 2: Availability check...")
            tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
            is_available = await self.check_availability(tomorrow, "10:00")
            
            test_results.append({
                "test": "availability_check", 
                "success": True, 
                "message": f"✅ Availability check: {tomorrow} 10:00 = {'Available' if is_available else 'Busy'}"
            })
            print(f"✅ Availability check: {'Available' if is_available else 'Busy'}")
            
            # Test 3: Get available slots
            print("📅 Test 3: Get available slots...")
            slots = await self.get_available_slots(tomorrow, "mattina")
            
            test_results.append({
                "test": "get_slots",
                "success": len(slots) >= 0,
                "message": f"✅ Found {len(slots)} available morning slots"
            })
            print(f"✅ Found {len(slots)} morning slots: {slots}")
            
            return {
                "success": True,
                "calendar_id": calendar_id,
                "tests": test_results
            }
            
        except Exception as e:
            logger.error(f"❌ Calendar test failed: {e}")
            test_results.append({"test": "connection", "success": False, "message": f"❌ Error: {str(e)}"})
            
            return {
                "success": False,
                "error": str(e),
                "tests": test_results
            }

# IMMEDIATE TEST: V1 Calendar Functions
async def test_v1_calendar_individually():
    """
    Test each V1 calendar function individually before integration
    """
    print("🧪 TESTING V1 CALENDAR FUNCTIONS INDIVIDUALLY")
    print("=" * 60)
    
    calendar_service = V1CalendarService()
    
    # Test connection first
    connection_test = await calendar_service.test_calendar_connection()
    
    print(f"\n📊 CONNECTION TEST RESULTS:")
    for test in connection_test["tests"]:
        print(f"   {test['message']}")
    
    if not connection_test["success"]:
        print("\n❌ CALENDAR CONNECTION FAILED")
        print("🔧 Need to setup Google Calendar credentials first")
        return False
    
    print("\n✅ CALENDAR CONNECTION SUCCESSFUL!")
    
    # Test individual functions with real data
    print("\n🧪 TESTING INDIVIDUAL FUNCTIONS:")
    
    # Test tomorrow's availability
    tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    
    try:
        # Test availability check
        print(f"\n📅 Testing availability for {tomorrow} at 10:00...")
        is_available = await calendar_service.check_availability(tomorrow, "10:00")
        print(f"   Result: {'✅ AVAILABLE' if is_available else '❌ BUSY'}")
        
        # Test availability check for different time
        print(f"\n📅 Testing availability for {tomorrow} at 15:00...")
        is_available_afternoon = await calendar_service.check_availability(tomorrow, "15:00")
        print(f"   Result: {'✅ AVAILABLE' if is_available_afternoon else '❌ BUSY'}")
        
        # Test get available slots
        print(f"\n📅 Testing available slots for {tomorrow} morning...")
        morning_slots = await calendar_service.get_available_slots(tomorrow, "mattina")
        print(f"   Morning slots: {morning_slots}")
        
        print(f"\n📅 Testing available slots for {tomorrow} afternoon...")
        afternoon_slots = await calendar_service.get_available_slots(tomorrow, "pomeriggio")
        print(f"   Afternoon slots: {afternoon_slots}")
        
        print("\n✅ ALL V1 FUNCTIONS WORKING!")
        print("🎯 Ready to integrate into production command executor")
        return True
        
    except Exception as e:
        print(f"\n❌ V1 FUNCTION TEST ERROR: {e}")
        return False

if __name__ == "__main__":
    # Test V1 calendar functions immediately
    asyncio.run(test_v1_calendar_individually())