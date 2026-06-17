import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs
import urllib.request
import urllib.parse
import urllib.error
import psycopg
import re
import os
import jwt
import datetime
from dotenv import load_dotenv

# Initialize dotenv to read keys from the local .env configuration file
load_dotenv()

# Extract secret database credentials and external API configuration securely from environment variables
books_db = os.getenv("books_db")
big_book_api_key = os.getenv("big_book_api_key")
big_book_url = os.getenv("big_book_url")
jwt_secret = os.getenv("JWT_SECRET", "fallback_secret_key")

class bookRequestsHandler(BaseHTTPRequestHandler):

    # Utility helper method to package JSON responses and set standardized HTTP headers uniformly
    def send_json(self, data, status_code = 200):
        self.send_response(status_code)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(data).encode('utf-8'))

    def is_authorized(self):
        auth_header = self.headers.get('Authorization')
        if not auth_header or not auth_header.startswith("Bearer "):
            self.send_json({"detail": "Missingor invalid Authorization header. Format: 'Bearer <token>'"}, status_code = 401)
            return False
        
        token = auth_header.split(" ")[1]
        try:
            jwt.decode(token, jwt_secret, algorithms=["HS256"])
            return True
        except jwt.ExpiredSignatureError:
            self.send_json({"detail": "Token has expired. Please login again."}, status_code=401)
            return False
        except jwt.InvalidTokenError:
            self.send_json({"detail": "Invalid token."}, status_code = 401)
            return False

    # Entry point for processing all incoming HTTP GET requests
    def do_GET(self):
        # Authorize GET
        if not self.is_authorized():
            return

        parsed_url = urlparse(self.path)
        path = parsed_url.path
        query_params = parse_qs(parsed_url.query)

        # 1. GET /books: Fetches and displays the entire local inventory sorted sequentially by ID
        if path == '/books':
            try:
                # pagination logic. Default to page 1
                try:
                    page = int(query_params.get('page', ['1'])[0])
                    limit = int(query_params.get('limit', ['5'])[0])
                    if page < 1 or limit < 1:
                        raise ValueError
                except ValueError:
                    self.send_json({"detail": "Page and limit must be positive integers."}, status_code = 400)
                    return
                
                # calculates offset
                offset = (page - 1) * limit

                with psycopg.connect(books_db) as conn:
                    with conn.cursor() as cur:
                        # pass limit and offset dynamically to SQL
                        query = "Select id, title, author, genre FROM books ORDER by id ASC LIMIT %s OFFSET %s;"
                        cur.execute(query, (limit, offset))
                        rows = cur.fetchall()

                        all_books = []
                        for row in rows:
                            all_books.append({
                                "id": row[0],
                                "title": row[1],
                                "author": row[2],
                                "genre": row[3]
                            })
                        self.send_json({
                            "page": page,
                            "limit": limit,
                            "total_returned": len(all_books),
                            "results": all_books
                        })
                        return
            except Exception as e:
                print(f"Database error encountered: {e}")
                self.send_json({"detail": "Internal Server Error"}, status_code=500)
                return

        # 2. GET /recommend: Filters local inventory dynamically using a case-insensitive genre query parameter
        elif path == '/recommend':
            genre_list = query_params.get('genre')

            if not genre_list:
                self.send_json({"detail": "Missing required query parameter: genre"}, status_code = 400)
                return
            requested_genre = genre_list[0]

            try:
                # pagination logic
                try:
                    page = int(query_params.get('page', ['1'])[0])
                    limit = int(query_params.get('limit', [5])[0])
                    if page < 1 or limit < 1:
                        raise ValueError
                except ValueError:
                    self.send_json({"detail": "Page and limit must be positive integers."}, status_code = 400)
                    return
                
                offset = (page - 1) * limit 

                with psycopg.connect(books_db) as conn:
                    with conn.cursor() as cur:
                        query = """
                            SELECT id, title, author, genre FROM books
                            WHERE LOWER(genre) = LOWER(%s)
                            ORDER BY id ASC
                            LIMIT %s OFFSET %s
                        """
                        cur.execute(query, (requested_genre, limit, offset))
                        rows = cur.fetchall()

                        recommendations = []
                        for row in rows:
                            recommendations.append({
                                "id": row[0],
                                "title": row[1],
                                "author": row[2],
                                "genre": row[3]
                            })
                        self.send_json({
                            "filter": requested_genre,
                            "page": page,
                            "limit": limit,
                            "total_returned": len(recommendations),
                            "results": recommendations
                        })
                        return
            except Exception as e:
                print(f"Database error encountered: {e}")
                self.send_json({"detail": "Internal Server Error"}, status_code = 500)
                return
        
        # 3. GET /search-external: Queries the Big Book API and parses its specific complex nested array schema
        elif path == '/search-external':
            query_list = query_params.get('query')
            if not query_list:
                self.send_json({"detail": "Missing required query parameter: query"}, status_code = 400)
                return
            user_query = query_list[0]

            try:
                # Safely escape user text queries for secure URL transmission
                encoded_query = urllib.parse.quote(user_query)
                external_url = f"{big_book_url}?query={encoded_query}&api-key={big_book_api_key}"

                print(f"Fetching from Big book API: {external_url}")

                # Attach browser headers to prevent security firewalls from blocking python-urllib connections
                req = urllib.request.Request(
                    external_url,
                    headers = {
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                        'Accept': 'application/json'
                    }
                )

                with urllib.request.urlopen(req) as response:
                    external_data = json.loads(response.read().decode('utf-8'))

                external_books = external_data.get('books', [])
                formatted_results = []
                
                # Navigate Big Book API's response structure (Iterating over nested list of edition groups)
                for edition_group in external_books:
                    if len(edition_group) == 0:
                        continue
                    # Grab the primary edition dictionary out of the nested array grouping
                    book_data = edition_group[0]

                    # Parse out the authors list which contains dictionaries mapping names
                    authors_list = book_data.get('authors', [])
                    author_name = authors_list[0].get('name', 'Unknown') if authors_list else 'Unknown'

                    # Extract the sub-dictionary containing the key rating score metric
                    rating = book_data.get('rating', 'Unknown')
                    rating = rating['average']
                    formatted_results.append({
                        "id": book_data.get('id', 'Unknown'),
                        "title": book_data.get('title', 'Unknown'),
                        "author": author_name,
                        "rating": rating
                    })
                
                self.send_json({"source": "Big Book API", "results": formatted_results})
                return
            
            # Catches explicit upstream API structural rejections or validation failure error numbers
            except urllib.error.HTTPError as he:
                print(f"External API HTTP Error: {he.code} - {he.reason}")
                self.send_json({"detail": "Failed to get records from external book registry"}, status_code = he.code)
                return
            # Catches unexpected script code errors like KeyErrors or type mismatches
            except Exception as e:
                print(f"Error calling external API {e}")
                self.send_json({"detail": "Internal Server Error during external tracking lookup"}, status_code = 500)
                return

        # 4. GET /book/{id}: Uses RegEx to match a variable trailing digit parameter route
        else:
            match = re.match(r'^/book/(\d+)$', self.path)
            if match:
                book_id = int(match.group(1))
                try:
                    with psycopg.connect(books_db) as conn:
                        with conn.cursor() as cur:
                            query = "SELECT id, title, author, genre FROM books WHERE id = %s;"
                            cur.execute(query, (book_id,))
                            row = cur.fetchone()

                            if row is None:
                                self.send_json({"detail": f"Book with ID {book_id} not found"}, status_code = 404)
                                return
                            
                            book_data = {
                                "id": row[0],
                                "title": row[1],
                                "author": row[2],
                                "genre": row[3]
                            }
                            self.send_json(book_data)
                            return
                except Exception as e:
                    print(f"Database error encountered: {e}")
                    self.send_json({"detail": "Internal Server Error"}, status_code = 500)
                    return
            
            # Catch-all route handler for unmatched or broken GET request string patterns
            self.send_json({"detail": "Not Found"}, status_code = 404)

    # Entry point for processing all incoming HTTP POST requests
    def do_POST(self):
        # /login - generates a JWT token for the user
        if self.path == '/login':
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length).decode('utf-8')

            try:
                data = json.loads(post_data)
                # hardcoded dummey authentication check
                if data.get("username") == "admin" and data.get("password") == "password123":
                    # Create valid token for 1 hour
                    expiration = datetime.datetime.utcnow() + datetime.timedelta(hours=1)
                    token = jwt.encode({"user": "admin", "exp": expiration}, jwt_secret, algorithm="HS256")
                    self.send_json({"message": "Login successfull, token will last for 1 hour", "access_token": token}, status_code = 200)
                    return
                else:
                    self.send_json({"detail": "Invalid username or password"}, status_code=401)
                    return
            except json.JSONDecodeError:
                self.send_json({"detail": "Invalid JSON format in request body"}, status_code = 400)
                return

        elif self.path == '/books':
            # protect this route
            if not self.is_authorized():
                return

            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length).decode('utf-8')

            try:
                data = json.loads(post_data)

                # Check if fields exist and if they are still valid text strings
                for field in ['title', 'author', 'genre']:
                    if field not in data:
                        self.send_json({"detail": f"Missing required field: {field}"}, status_code = 400)
                        return
                    if not isinstance(data[field], str) or not data[field].strip():
                        self.send_json({"detail": f"Field '{field}' must be a non-empty string."}, status_code=400)
                        return
                
                input_title = data["title"]
                input_author = data["author"]
                input_genre = data["genre"]

                with psycopg.connect(books_db) as conn:
                    with conn.cursor() as cur:
                        # Prevent duplicate entry creation through strict conditional constraint matching
                        check_query = """
                            SELECT id FROM books
                            WHERE LOWER(title) = LOWER(%s)
                            AND LOWER(author) = LOWER(%s)
                            AND LOWER(genre) = LOWER(%s);
                        """
                        cur.execute(check_query, (input_title, input_author, input_genre))
                        existing_book = cur.fetchone()

                        if existing_book is not None:
                            self.send_json({"detail": f"The book '{input_title}' already exists in the database."}, status_code = 409)
                            return
                        
                        insert_query = """
                            INSERT INTO books (title, author, genre)
                            VALUES (%s, %s, %s)
                            RETURNING id;
                        """
                        cur.execute(insert_query, (input_title, input_author, input_genre))
                        
                        # Fetch the database-generated auto-incremented primary key index
                        result = cur.fetchone()
                        if result is None:
                            print("Error: database executed INSERT but RETURNING id came back empty")
                            self.send_json({"detail": "Database failed to return new book ID"}, status_code = 500)
                            return
                        new_id = result[0]

                        new_book = {
                            "id": new_id,
                            "title": input_title,
                            "author": input_author,
                            "genre": input_genre
                        }

                        self.send_json(new_book, status_code = 201)
                        return
            except json.JSONDecodeError:
                self.send_json({"detail": "Invalid JSON format in request body"}, status_code = 400)
                return
            except Exception as e:
                print(f"Database error encountered: {e}")
                self.send_json({"detail": "Internal Server Error"}, status_code = 500)
                return
            
        # Rejects valid paths attempting actions on unsupported HTTP verbs
        if self.path == '/recommend' or self.path == '/search-external' or re.match(r'^/book/(\d+)$', self.path):
            self.send_json({"detail": "Method Not Allowed. Use GET instead."}, status_code = 405)
            return
        

        self.send_json({"detail": "Not Found"}, status_code = 404)

    
    # Entry point for processing all incoming HTTP DELETE requests
    def do_DELETE(self):
        if not self.is_authorized():
            return

        # DELETE /books/{ID} deletes book based on id uses regex
        match = re.match(r'^/book/(\d+)$', self.path)
        if match:
            book_id = int(match.group(1))

            try:
                with psycopg.connect(books_db) as conn:
                    with conn.cursor() as cur:
                        delete_query = "DELETE FROM books WHERE id = %s;"
                        cur.execute(delete_query, (book_id,))
                        # Inspect the modification driver counter to confirm if row deletion took place
                        deleted_rows_count = cur.rowcount

                        if deleted_rows_count == 0:
                            self.send_json({"detail": f"Book with ID {book_id} not found."}, status_code = 404)
                            return
                        self.send_json({"detail": f"Successfully deleted book with ID {book_id}."}, status_code = 200)
                        return

            except Exception as e:
                print(f"Database error encountered: {e}")
                self.send_json({"detail": "Internal server Error"}, status_code = 500)
                return
        
        # Guard clause returning a 405 response error for endpoints that do not accept DELETE methods
        if self.path == '/recommend' or self.path == '/search-external' or self.path == '/books':
            self.send_json({"detail": "Method Not Allowed. Use GET instead."}, status_code = 405)
            return
        
        self.send_json({"detail": "Not Found"}, status_code = 404)
        

    def do_PATCH(self):
        # protect this route
        if not self.is_authorized():
            return

        #match a variable route like: PATCH /book/12
        match = re.match(r'^/book/(\d+)$', self.path)
        if match:
            book_id = int(match.group(1))
            content_Length = int(self.headers['Content-Length'])
            patch_data = self.rfile.read(content_Length).decode('utf-8')

            try:
                data = json.loads(patch_data)

                # Ensure the user actually provided fields to update
                if not data:
                    self.send_json({"detail": "No fields provided for update"}, status_code=400)
                    return
                
                update_fields = []
                query_values = []

                # Validation for PATCH
                for field in ['title', 'author', 'genre']:
                    if field in data:
                        if not isinstance(data[field], str) or not data[field].strip():
                            self.send_json({"detail": f"If providing '{field}', it must be a non-empty string."}, status_code=400)
                            return
                        update_fields.append(f"{field} = %s")
                        query_values.append(data[field].strip())
                    
                if not update_fields:
                    self.send_json({"detail": "No valid fields (title, author, genre) provided for update"}, status_code = 400)
                    return
                
                
                sql_set_clause = ", ".join(update_fields)
                update_query = f"UPDATE books SET {sql_set_clause} WHERE id = %s RETURNING id, title, author, genre;"

                # Append the book_id parameter to complete WHERE clause target
                query_values.append(book_id)

                with psycopg.connect(books_db) as conn:
                    with conn.cursor() as cur:
                        cur.execute(update_query, tuple(query_values))
                        row = cur.fetchone()

                        if row is None:
                            self.send_json({"detail": f"Book with ID {book_id} not found"}, status_code = 404)
                            return
                        
                        updated_book = {
                            "id": row[0],
                            "title": row[1],
                            "author": row[2],
                            "genre": row[3]
                        }
                        self.send_json(updated_book, status_code=200)
                        return
            except json.JSONDecodeError:
                self.send_json({"detail": "Invalid JSON format in request body"}, status_code = 400)
                return
            except Exception as e:
                print(f"Database error encountered during patch: {e}")
                self.send_json({"detail": "Internal Server Error"}, status_code = 500)
                return
        
        if self.path == '/books' or self.path == '/recommend' or self.path == '/search-external':
            self.send_json({"detail": "Method Not Allowed. Use GET or POST instead."}, status_code = 405)
            return
        
        self.send_json({"detail": "Not Found"}, status_code = 404)

# Starts up the server stack and establishes the loop listening for socket transmissions
def run(server_class = HTTPServer, handler_class = bookRequestsHandler, port = 8080):
    server_address = ('', port)
    httpd = server_class(server_address, handler_class)
    print(f"Server successfully running on http://localhost:{port}...")
    try:
        httpd.serve_forever()
    # Intercepts Ctrl+C events in the terminal environment to shutdown cleanly without leaking network ports
    except KeyboardInterrupt:
        print("\nStopping server...")
        httpd.server_close()

if __name__ == '__main__':
    run()